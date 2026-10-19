#!/usr/bin/env python3
import os
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
"""Deep audit metrics from state.json -> audit_deep.json"""
import json, collections, statistics
s=json.load(open(DATA+'/state.json'))
notes=s['notes']; L=s['listings']; minted=s['minted']; nfs=s['nullifiers']; roots=s['roots']
out={}
H0=min(n['height'] for n in notes); H1=s['lastHeight']
out['firstHeight']=H0; out['lastHeight']=H1; out['blocksIndexed']=H1-H0+1
# timeline buckets of 50 blocks
B=50; tl=collections.OrderedDict()
for n in notes:
    b=H0+((n['height']-H0)//B)*B
    tl.setdefault(b,collections.Counter())[n['kind']]+=1
out['timeline']=[{'from':b,'to':b+B-1,**dict(c)} for b,c in sorted(tl.items())]
# commitment integrity
cms=[n['cm'] for n in notes]; out['dupCommitments']=len(cms)-len(set(cms))
pos=[n['pos'] for n in notes]; out['posContiguous']= pos==list(range(len(notes)))
heights=[n['height'] for n in notes]; out['heightsMonotonic']=all(a<=b for a,b in zip(heights,heights[1:]))
tx=collections.Counter(n['txid'] for n in notes); out['uniqueTxids']=len(tx); out['maxNotesPerTx']=max(tx.values())
out['notesPerTxHist']=dict(collections.Counter(tx.values()))
# shielded pool
out['notes']=len(notes); out['nullifiers']=len(nfs); out['unspentNotes']=len(notes)-len(nfs)
out['spentPct']=round(100*len(nfs)/len(notes),1)
# root history
rh=[{'h':r[0],'leaves':r[2]} for r in roots]
out['rootHistory']=rh; out['rootChanges']=sum(1 for a,b in zip(roots,roots[1:]) if a[1]!=b[1])
out['rootLeavesMatchNotes']= roots[-1][2]==len(notes)
out['latestRoot']=str(roots[-1][1])
# minted id checks
ms=set(minted); out['mintOutOfRange']=sum(1 for m in minted if not 0<=int(m)<=3332)
out['unmintedIds']=sorted(set(range(0,3333))-set(int(m) for m in minted))
# listings
st=collections.Counter(l['state'] for l in L); out['listingStates']=dict(st)
sold=[l for l in L if l['state']=='SOLD']; act=[l for l in L if l['state']=='ACTIVE']
p=[int(l['price'])/1e8 for l in sold]
edges=[0,0.0005,0.001,0.0015,0.002,0.003,0.005,0.01,1e9]
labels=['<0.0005','0.0005-0.001','0.001-0.0015','0.0015-0.002','0.002-0.003','0.003-0.005','0.005-0.01','>0.01']
def hist(v):
    c=[0]*len(labels)
    for x in v:
        for i in range(len(labels)):
            if edges[i]<=x<edges[i+1]: c[i]+=1;break
    return dict(zip(labels,c))
out['soldPriceHist']=hist(p); out['soldMedianBTC']=round(statistics.median(p),6) if p else None
ap=sorted(int(l['price'])/1e8 for l in act)
out['activeFloorLadder']=ap[:10]; out['activePriceHist']=hist(ap)
out['activeOutliers']=sum(1 for x in ap if x>=1)
# sales per 50 blocks (by listing height of sold)
sb=collections.Counter(H0+((l['height']-H0)//B)*B for l in sold)
out['salesByListingBlock']=[{'from':k,'sales':v} for k,v in sorted(sb.items())]
# relist churn
ac=collections.Counter(l['assetId'] for l in L); out['assetsEverListed']=len(ac)
out['relistedAssets']=sum(1 for v in ac.values() if v>1); out['maxListingsOneAsset']=max(ac.values())
# seller privacy hygiene
sc=collections.Counter(l['sellerScript'] for l in L)
out['uniqueSellerScripts']=len(sc)
top=sc.most_common(10); tot=len(L)
out['top10SellerShare']=round(100*sum(v for _,v in top)/tot,1)
out['topSellers']=[{'script':k[:8]+'…'+k[-6:],'listings':v} for k,v in top[:5]]
out['scriptsReused']=sum(1 for v in sc.values() if v>1)
out['scriptReusePct']=round(100*sum(v for v in sc.values() if v>1)/tot,1)
rk=collections.Counter(l['returnKey'] for l in L); out['returnKeyReuse']=sum(1 for v in rk.values() if v>1)
out['taprootSellerPct']=round(100*sum(v for k,v in sc.items() if k.startswith('5120'))/tot,1)
lv=collections.Counter(l['lock']['value'] for l in L); out['lockValues']=dict(lv)
tk=collections.Counter(l['tickets'] for l in L); out['ticketsHist']=dict(sorted(tk.items()))
# consistency
out['soldMissingTxid']=sum(1 for l in sold if not l.get('soldTxid'))
cl=[l for l in L if l['state']=='CLAIMED']; out['claimedMissingTxid']=sum(1 for l in cl if not l.get('claimTxid'))
out['activeWithCloseTxid']=sum(1 for l in act if l.get('closedTxid') or l.get('soldTxid'))
lnf=collections.Counter(l['nf'] for l in L); out['dupListingNullifiers']=sum(1 for v in lnf.values() if v>1)
nfset=set(str(x) for x in nfs); out['soldOrClaimedNfSpent']=sum(1 for l in sold+cl if str(l['nf']) in nfset); out['soldOrClaimedTotal']=len(sold)+len(cl)
out['activeNfAlreadySpent']=sum(1 for l in act if str(l['nf']) in nfset)


# ---- protocol health: indexer lag, anonymity set, stale escrows ----
import urllib.request
try: tip=int(urllib.request.urlopen('https://mempool.space/api/blocks/tip/height',timeout=15).read())
except Exception: tip=None
out['btcTipHeight']=tip; out['indexerLagBlocks']=(tip-H1) if tip else None
out['anonymitySet']=out['unspentNotes']
kinds=collections.Counter(n['kind'] for n in notes); out['noteKinds']=dict(kinds)
ref=tip or H1
ages=sorted([((ref-l['height']),l) for l in act], key=lambda x:x[0])
bk={'<1 day':0,'1-7 days':0,'7-30 days':0,'>30 days':0}
for a,_ in ages:
    k='<1 day' if a<144 else '1-7 days' if a<1008 else '7-30 days' if a<4320 else '>30 days'; bk[k]+=1
out['activeListingAgeBuckets']=bk
out['staleListings']=sum(1 for a,_ in ages if a>=1008)
out['oldestActiveListings']=[{'assetId':l['assetId'],'height':l['height'],'ageBlocks':a,'lockTxid':l['lock']['txid'],'lockValue':l['lock']['value']} for a,l in reversed(ages[-5:])]
out['medianActiveAgeBlocks']=ages[len(ages)//2][0] if ages else None
# ---- audit history, anomalies, report hash ----
import hashlib, datetime, os
checks={'commitmentsUnique':out['dupCommitments']==0,'positionsContiguous':bool(out['posContiguous']),
 'heightsMonotonic':bool(out['heightsMonotonic']),'rootMatchesNotes':bool(out['rootLeavesMatchNotes']),
 'noDupListingNullifiers':out['dupListingNullifiers']==0,
 'soldClaimedSpent':out['soldOrClaimedNfSpent']==out['soldOrClaimedTotal'],
 'mintInRange':out['mintOutOfRange']==0,'soldHaveTxid':out['soldMissingTxid']==0,'claimedHaveTxid':out['claimedMissingTxid']==0}
core={k:out[k] for k in ('lastHeight','notes','nullifiers','latestRoot','dupCommitments','rootLeavesMatchNotes','soldOrClaimedNfSpent','soldOrClaimedTotal','unmintedIds','dupListingNullifiers','mintOutOfRange')}
core['checks']=checks
digest=hashlib.sha256(json.dumps(core,sort_keys=True,default=str).encode()).hexdigest()
now=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
HP=DATA+'/audit_history.json'
hist=json.load(open(HP)) if os.path.exists(HP) else []
prev=hist[-1] if hist else None
anoms=[]
if prev:
    dn=out['notes']-prev['notes']; dnf=out['nullifiers']-prev['nullifiers']
    if dn<0: anoms.append(f"Note count fell by {-dn}")
    if dnf<0: anoms.append(f"Nullifier count fell by {-dnf}")
    if out['latestRoot']!=prev['root'] and dn==0: anoms.append('Root changed with no new leaves')
    if out['lastHeight']<prev['height']: anoms.append('Indexer height went backwards')
    if out['indexerLagBlocks'] and out['indexerLagBlocks']>36: anoms.append(f"Indexer {out['indexerLagBlocks']} blocks behind tip")
    for k,v in checks.items():
        if prev['checks'].get(k)!=v: anoms.append(f"Check {k} flipped to {'PASS' if v else 'FAIL'}")
entry={'time':now,'height':out['lastHeight'],'anonymitySet':out['anonymitySet'],'lag':out['indexerLagBlocks'],'notes':out['notes'],'nullifiers':out['nullifiers'],'root':out['latestRoot'],
 'passed':sum(checks.values()),'total':len(checks),'checks':checks,'sha256':digest,'anomalies':anoms}
if not prev or prev['sha256']!=digest or prev['height']!=entry['height']: hist.append(entry)
hist=hist[-240:]; json.dump(hist,open(HP,'w'),indent=1)
streak=0
for e in reversed(hist):
    if e['passed']==e['total']: streak+=1
    else: break
out['history']=[{k:e[k] for k in ('time','height','notes','nullifiers','passed','total','sha256','anomalies')} | {'anonymitySet':e.get('anonymitySet',e['notes']-e['nullifiers']),'lag':e.get('lag')} for e in hist]
out['passStreak']=streak; out['checks']=checks; out['reportSha256']=digest; out['reportCore']=core; out['generatedAt']=now
out['anomalyLog']=[{'time':e['time'],'height':e['height'],'msg':a} for e in hist for a in e['anomalies']]
json.dump(out,open(DATA+'/audit_deep.json','w'),indent=1)
print(json.dumps({k:v for k,v in out.items() if k not in('timeline','rootHistory','salesByListingBlock')},indent=1))
