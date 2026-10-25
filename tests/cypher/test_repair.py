import copy
import importlib
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
import stake_discovery as d
import staketasjson as candidate

def node(n=1):
    return {'address': '0x'+format(n,'064x'), 'contents': {'type': {'repr':d.STAKE_TYPE}}, 'owner': {'__typename':'AddressOwner','address':{'address':d.OWNER}}}
def page(nodes=None, more=False, cursor=None):
    return {'data':{'address':{'objects':{'nodes':[] if nodes is None else nodes,'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}}}
class RepairTests(unittest.TestCase):
    def reject(self, *pages):
        it=iter(pages)
        with self.assertRaises(d.DiscoveryError): d.discover(lambda c,t: next(it))
    def test_empty(self): self.assertEqual(d.discover(lambda c,t:page()),[])
    def test_multipage(self):
        calls=[]
        def fetch(c,t):
            calls.append(c)
            return page([node(1)],True,'a') if c is None else page([node(2)],False,'b')
        self.assertEqual(d.discover(fetch),[node(1)['address'],node(2)['address']]);self.assertEqual(calls,[None,'a'])
    def test_transport_and_partial_failure(self):
        for first in (False,True):
            def fetch(c,t):
                if first and c is None:return page([node()],True,'a')
                raise TimeoutError('network')
            with self.assertRaises(d.DiscoveryError):d.discover(fetch)
    def test_api_errors_even_with_partial_data(self):
        for err in ([{'message':'bad'}],None,'bad',{}):
            p=page([node()]);p['errors']=err;self.reject(p)
    def test_schema(self):
        for p in (None,[],{}, {'data':None},{'data':{'address':None}}, {'data':{'address':{'objects':None}}}):self.reject(p)
        for value in (None,{},'bad'):
            p=page();p['data']['address']['objects']['nodes']=value;self.reject(p)
        for value in ('false',0,None):self.reject(page(more=value))
    def test_identity_type_owner(self):
        cases=[]
        for value in ('0x1','0x'+ 'g'*64,'0x'+ '1'*64+'; echo bad',None):
            n=node();n['address']=value;cases.append(n)
        n=node();n['owner']['address']['address']='0x'+'f'*64;cases.append(n)
        n=node();n['owner']['__typename']='Shared';cases.append(n)
        n=node();n['contents']['type']['repr']='0x2::coin::Coin';cases.append(n)
        cases.extend([{},None])
        for n in cases:self.reject(page([n]))
    def test_duplicates(self):
        self.reject(page([node(),node()]));self.reject(page([node()],True,'a'),page([node()]))
    def test_cursor_loop_and_missing(self):
        self.reject(page([node()],True,None));self.reject(page([],True,'a'))
        self.reject(page([node(1)],True,'a'),page([node(2)],True,'a'))
        self.reject(page([node(1)],True,'a'),page([node(2)],True,'b'),page([node(3)],True,'a'))
        self.reject(page([node()],True,'x'*8193))
    def test_partial_result_followed_by_invalid_page(self):
        self.reject(page([node()],True,'a'),{'errors':[{'message':'unavailable'}]})
        self.reject(page([node()],True,'a'),{'data':None})
    def test_http_transport_is_read_query_only(self):
        class Response:
            status=200
            headers={'Content-Type':'application/json'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return json.dumps(page()).encode()
        with patch('urllib.request.build_opener') as factory:
            factory.return_value.open.return_value=Response()
            self.assertEqual(d.fetch_page(None,20),page())
            request=factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,d.ENDPOINT)
            body=json.loads(request.data)
            self.assertTrue(body['query'].startswith('query'))
            self.assertNotIn('mutation',body['query'])
            self.assertEqual(body['variables']['owner'],d.OWNER)
    def test_limits(self):
        self.reject(page([node(i) for i in range(51)]))
        with patch.object(d,'MAX_PAGES',1):self.reject(page([node()],True,'a'))
        times=iter([0,0,121])
        with self.assertRaises(d.DiscoveryError):d.discover(lambda c,t:page(),lambda:next(times))
    def test_decoding(self):
        for status,ctype,body in [(404,'',b''),(429,'application/json',b'{}'),(200,'text/html',b'{}'),(200,'application/json',b''),(200,'application/json',b'\xff'),(200,'application/json',b'{"data":1,"data":2}'),(200,'application/json',b'NaN'),(200,'application/json',b'x'*(d.MAX_BODY+1))]:
            with self.assertRaises(d.DiscoveryError):d.decode_response(status,ctype,body)
        self.assertEqual(d.decode_response(200,'application/json; charset=utf-8',b'{}'),{})
    def test_redirect(self):
        with self.assertRaises(d.DiscoveryError):d.NoRedirect().redirect_request(None,None,302,'',{},'http://evil')
    def test_import_inert(self):
        with patch('subprocess.run',side_effect=AssertionError),patch('os.popen',side_effect=AssertionError),patch('urllib.request.build_opener',side_effect=AssertionError),patch('logging.basicConfig',side_effect=AssertionError):
            importlib.reload(candidate)
    def test_main_failure_never_pays(self):
        with patch.object(candidate.socket,'gethostname',return_value='fixture-cypher'),patch.object(candidate,'discover',side_effect=d.DiscoveryError('bad')),patch.object(candidate,'run_payout') as payout,redirect_stderr(io.StringIO()):
            self.assertEqual(candidate.main([]),1);payout.assert_not_called()
    def test_readonly_never_pays(self):
        with patch.object(candidate,'snapshot',return_value={}),patch.object(candidate,'discover',return_value=[node()['address']]),patch.object(candidate,'run_payout') as payout,redirect_stdout(io.StringIO()):
            self.assertEqual(candidate.main(['--discover-only']),0);payout.assert_not_called()
    def test_wrong_host_never_queries_or_pays(self):
        with patch.object(candidate.socket,'gethostname',return_value='fixture-jsm'),patch.object(candidate,'discover') as query,patch.object(candidate,'run_payout') as payout,redirect_stderr(io.StringIO()):
            self.assertEqual(candidate.main([]),2);query.assert_not_called();payout.assert_not_called()
    def test_main_success_with_mock_payout_only(self):
        ids=[node()['address']]
        with patch.object(candidate.socket,'gethostname',return_value='fixture-cypher'),patch.object(candidate,'discover',return_value=ids),patch.object(candidate,'run_payout') as payout,patch('logging.basicConfig'),redirect_stdout(io.StringIO()):
            self.assertEqual(candidate.main([]),0);self.assertEqual(payout.call_count,1);self.assertEqual(payout.call_args.args[0],ids)
if __name__=='__main__':unittest.main(verbosity=2)
