import copy
import unittest
from unittest.mock import patch
import wallet_snapshot as w
from stake_discovery import DiscoveryError,OWNER
A='0x'+'a'*64;B='0x'+'b'*64
D='1'*31+'2'
def node(oid=A,amount=10):return {'address':oid,'version':9,'digest':D,'owner':{'__typename':'AddressOwner','address':{'address':OWNER}},'contents':{'type':{'repr':w.CANONICAL_TYPE},'json':{'id':oid,'balance':str(amount)}}}
def page(nodes=None,total=0,more=False,cursor=None,checkpoint=20):
    return {'data':{'checkpoint':{'sequenceNumber':checkpoint},'address':{'balance':{'coinBalance':str(total),'addressBalance':'0','totalBalance':str(total)},'objects':{'nodes':[] if nodes is None else nodes,'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}}}
class SnapshotTests(unittest.TestCase):
    def reject(self,*pages,**kwargs):
        iterator=iter(pages)
        with self.assertRaises(DiscoveryError):w.snapshot(lambda c,t:next(iterator),**kwargs)
    def test_empty(self):self.assertEqual(w.snapshot(lambda c,t:page()),{})
    def test_all_pages_reconcile_total(self):
        calls=[]
        def fetch(c,t):
            calls.append(c);return page([node(A,10)],30,True,'a') if c is None else page([node(B,20)],30,False,'b')
        result=w.snapshot(fetch);self.assertEqual(sum(c['balance'] for c in result.values()),30);self.assertEqual(calls,[None,'a'])
        self.assertEqual(result[A]['version'],9);self.assertEqual(result[A]['digest'],D)
    def test_missing_page_objects_rejected(self):self.reject(page([node()],total=20))
    def test_duplicate_within_and_between_pages(self):
        self.reject(page([node(),node()],20));self.reject(page([node()],20,True,'a'),page([node()],20))
    def test_cursor_cycles(self):self.reject(page([node()],20,True,'a'),page([node(B)],20,True,'a'))
    def test_missing_or_empty_cursor(self):
        for cursor in (None,'',False,{},'x'*8193):self.reject(page([node()],10,True,cursor))
    def test_empty_page_cannot_advance(self):self.reject(page([],10,True,'a'))
    def test_partial_data_graphql_error(self):
        p=page([node()],10);p['errors']=[{'message':'oops'}];self.reject(p)
    def test_failed_second_page_returns_no_partial_wallet(self):self.reject(page([node()],20,True,'a'),{'errors':[{'message':'down'}]})
    def test_schema_missing_or_null(self):
        for p in (None,[],{}, {'data':None},{'data':{'checkpoint':{'sequenceNumber':20},'address':None}}):self.reject(p)
    def test_bad_pageinfo(self):
        for more in (None,1,'false'):self.reject(page(more=more))
    def test_coin_identity_and_owner(self):
        for field,value in [('address','bad;sh'),('digest','0'*44),('digest','z'*44),('version',-1),('version',True),('version','nan')]:
            n=node();n[field]=value;self.reject(page([n],10))
        n=node();n['owner']['address']['address']=A;self.reject(page([n],10))
        n=node();n['owner']['__typename']='Shared';self.reject(page([n],10))
        n=node();n['contents']['json']['id']=B;self.reject(page([n],10))
    def test_wrong_type_or_malformed_contents(self):
        n=node();n['contents']['type']['repr']='0x2::coin::Coin<OTHER>';self.reject(page([n],10))
        n=node();n['contents']['json']=None;self.reject(page([n],10))
    def test_negative_fractional_boolean_and_huge_amounts(self):
        for amount in (-1,1.5,True,2**64,'1e9'):
            n=node();n['contents']['json']['balance']=amount;self.reject(page([n],10))
    def test_nonzero_address_balance_reported_separately(self):
        p=page([node()],10);p['data']['address']['balance'].update(addressBalance='1',totalBalance='11');result=w.snapshot(lambda c,t:p);self.assertEqual(w.address_balance(result),1);self.assertEqual(sum(v['balance'] for v in result.values()),10)
    def test_inconsistent_aggregate(self):
        p=page([node()],10);p['data']['address']['balance']['totalBalance']='9';self.reject(p)
    def test_wallet_changes_during_pagination(self):self.reject(page([node()],20,True,'a'),page([node(B)],21))
    def test_index_checkpoint_too_old(self):self.reject(page(),min_checkpoint=21)
    def test_index_checkpoint_sufficient(self):self.assertEqual(w.snapshot(lambda c,t:page(),min_checkpoint=20),{})
    def test_page_size_and_page_count_limit(self):
        self.reject(page([node('0x'+format(i,'064x')) for i in range(51)],510))
        with patch.object(w,'MAX_PAGES',1):self.reject(page([node()],20,True,'a'))
    def test_deadline_and_transport_failure(self):
        times=iter([0,0,121])
        with self.assertRaises(DiscoveryError):w.snapshot(lambda c,t:page(),clock=lambda:next(times))
        with self.assertRaises(DiscoveryError):w.snapshot(lambda c,t:(_ for _ in ()).throw(TimeoutError()))
