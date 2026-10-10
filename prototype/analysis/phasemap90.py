import json,sys,math,datetime as dt,statistics as st
S={}
for f in sys.argv[1:]:
    for e in json.load(open(f))['data']['entities']:
        S.setdefault(e['entity_id'],{}).update({r['start']:r['mean'] for r in e['statistics'] if r.get('mean') is not None})
L=['sensor.current_l1','sensor.current_l2','sensor.current_l3']
ts=sorted(set.intersection(*[set(S[l]) for l in L]))
print(f"main-fuse hours: {len(ts)} = {len(ts)/24:.1f} days, {dt.datetime.fromtimestamp(ts[0]/1000):%Y-%m-%d} .. {dt.datetime.fromtimestamp(ts[-1]/1000):%Y-%m-%d %H:00}")
amp=lambda k,v: v if 'current' in k else v/230
def corr(a,b):
    n=len(a); ma=sum(a)/n; mb=sum(b)/n
    sa=math.sqrt(sum((x-ma)**2 for x in a)); sb=math.sqrt(sum((y-mb)**2 for y in b))
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/(sa*sb) if sa and sb else float('nan')
print("\n== phase mapping (hourly steps where the sub-meter changed > 0.8 A) ==")
for k,v in sorted(S.items()):
    if k in L: continue
    t=[x for x in ts if x in v]
    if not t: print(f"{k[7:]:50} no data"); continue
    xs=[amp(k,v[x]) for x in t]; mx=max(xs)
    hrs=sum(1 for x in xs if x>0.5)
    pairs=[(t[i-1],t[i]) for i in range(1,len(t)) if t[i]-t[i-1]==3600000]
    dx=[amp(k,v[b])-amp(k,v[a]) for a,b in pairs]; idx=[i for i,x in enumerate(dx) if abs(x)>0.8]
    out=[]
    for l in L:
        dl=[S[l][b]-S[l][a] for a,b in pairs]
        num=sum(dx[i]*dl[i] for i in idx); den=sum(dx[i]**2 for i in idx)
        out.append((corr([dx[i] for i in idx],[dl[i] for i in idx]) if len(idx)>2 else float('nan'), num/den if den else float('nan')))
    first=dt.datetime.fromtimestamp(t[0]/1000)
    best=max(range(3),key=lambda i: out[i][1] if not math.isnan(out[i][1]) else -9)
    tag="idle" if not idx else f"L{best+1}"
    print(f"{k[7:]:50} from {first:%m-%d} max(h-mean) {mx:5.1f}A  hours>0.5A {hrs:4d}  steps {len(idx):3d}  gain L1/L2/L3 = "+"/".join(f"{g:+.2f}" for r,g in out)+f"  -> {tag}")
