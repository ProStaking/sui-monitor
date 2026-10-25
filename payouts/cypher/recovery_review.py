"""Pure recovery assessment only: never edits a journal or submits a transaction."""
from stake_discovery import OWNER, DiscoveryError

def continuation_plan(state, proofs, remaining_ids, sender_digests):
    if not isinstance(state,dict) or state.get('version')!=1 or state.get('status')!='blocked':
        raise DiscoveryError('Expected existing blocked version1 journal')
    ids=state.get('stake_ids');records=state.get('transactions')
    if not isinstance(ids,list) or len(ids)!=len(set(ids)) or not isinstance(records,list) or not records:
        raise DiscoveryError('Malformed recovery journal')
    completed=[];digests=[];watermark=0
    for r in records:
        if r.get('kind')!='withdraw' or r.get('status')!='confirmed_success':
            raise DiscoveryError('Unknown, failed or transfer outcomes require separate review')
        tx=r.get('digest');oid=r.get('object_id');proof=proofs.get(tx)
        if not tx or tx in digests or oid not in ids or oid in completed:
            raise DiscoveryError('Duplicate or unrelated recovery record')
        if not proof or proof.get('status')!='SUCCESS' or proof.get('sender')!=OWNER or proof.get('digest')!=tx:
            raise DiscoveryError('Missing independently verified success/sender')
        cp=proof.get('checkpoint')
        if type(cp) is not int or cp!=r.get('checkpoint') or cp<watermark:
            raise DiscoveryError('Checkpoint mismatch/regression')
        if proof.get('deleted_stake')!=oid or proof.get('net_gas_mist')!=r.get('net_gas_mist'):
            raise DiscoveryError('Withdrawal effects mismatch')
        completed.append(oid);digests.append(tx);watermark=cp
    if set(sender_digests)!=set(digests) or len(sender_digests)!=len(digests):
        raise DiscoveryError('Unexpected sender transactions; reconcile first')
    remaining=[oid for oid in ids if oid not in completed]
    if completed!=ids[:len(completed)] or len(remaining_ids)!=len(set(remaining_ids)) or set(remaining_ids)!=set(remaining):
        raise DiscoveryError('Remaining stakes differ from confirmed withdrawal prefix')
    return {'status':'review_required_not_authorization','preserve_transactions':records,
            'remaining_stake_ids':remaining,'min_checkpoint':watermark,
            'skip_confirmed_digests':digests}
