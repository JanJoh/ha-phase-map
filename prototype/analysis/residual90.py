import json,sys,datetime as dt,statistics as st,collections
S={}
for f in sys.argv[1:]:
    for e in json.load(open(f))['data']['entities']:
        S.setdefault(e['entity_id'],{}).update({r['start']:r['mean'] for r in e['statistics'] if r.get('mean') is not None})
M={1:['garo_entity_compact_charger_l1_current','effektmatare_spis_phase_c_effekt','garaget_garaget_l3_effekt','effektmatare_infravarmare_phase_c_effekt','shellypro3em_ece334ea8c9c_phase_a_current'],
   2:['effektmatare_diskmaskinen_effekt','effektmatare_tvattmaskinen_effekt','effektmatare_varmvattenberedare_effekt','vattenkokaren_effekt','shellypro3em_ece334ea8c9c_phase_c_current'],
   3:['effektmatare_spis_phase_b_effekt','shellypro3em_ece334ea8c9c_phase_b_current']}
amp=lambda k,v: v if 'current' in k else v/230
ts=sorted(set.intersection(*[set(S[f'sensor.current_l{p}']) for p in (1,2,3)]))
R={}
for t in ts:
    r=[]
    for p in (1,2,3):
        x=S[f'sensor.current_l{p}'][t]-sum(amp(k,S['sensor.'+k].get(t,0) or 0) for k in M[p])
        r.append(x)
    R[t]=r
base=[st.median(R[t][i] for t in ts) for i in range(3)]
print("baseline unexplained current (median) L1/L2/L3:", " / ".join(f"{b:.1f} A" for b in base))
ex=lambda t:[R[t][i]-base[i] for i in range(3)]
cand=[t for t in ts if min(ex(t))>=3]
print(f"\nhours with >=3 A unexplained on ALL three phases: {len(cand)}")
# group consecutive hours into sessions
sess=[];cur=[]
for t in cand:
    if cur and t-cur[-1]>3600000: sess.append(cur);cur=[]
    cur.append(t)
if cur: sess.append(cur)
for s in sess:
    a=dt.datetime.fromtimestamp(s[0]/1000); b=dt.datetime.fromtimestamp(s[-1]/1000)+dt.timedelta(hours=1)
    pk=[max(ex(t)[i] for t in s) for i in range(3)]
    bvp='BVP per-phase known' if s[0]>=min(S['sensor.shellypro3em_ece334ea8c9c_phase_b_current']) else 'before BVP per-phase data'
    print(f"  {a:%a %Y-%m-%d %H:%M}-{b:%H:%M}  {len(s)} h  peak extra L1/L2/L3 = "+"/".join(f"{x:4.1f}" for x in pk)+f" A  ({bvp})")
# single-phase large unexplained loads
print("\nhours with >=6 A unexplained on exactly one or two phases (by phase pattern):")
pat=collections.Counter(); ex_h=collections.defaultdict(list)
for t in ts:
    e=ex(t); key=tuple(i+1 for i in range(3) if e[i]>=6)
    if key and len(key)<3: pat[key]+=1; ex_h[key].append(t)
for k,n in pat.most_common():
    hrs=collections.Counter(dt.datetime.fromtimestamp(t/1000).hour for t in ex_h[k])
    pre=sum(1 for t in ex_h[k] if t<min(S['sensor.shellypro3em_ece334ea8c9c_phase_b_current']))
    print(f"  phases {k}: {n} h ({pre} before 1 Sep)  most common hours {sorted(hrs.most_common(4))}")
print("\nsensitivity check:")
for thr in (1.5,2,2.5):
    n=sum(1 for t in ts if min(ex(t))>=thr); print(f"  hours with >= {thr} A extra on all three phases: {n}")
mx=[max(ex(t)[i] for t in ts) for i in range(3)]
print("  largest unexplained hourly mean per phase since 1 Sep:", " / ".join(f"{max(ex(t)[i] for t in ts if t>=min(S['sensor.shellypro3em_ece334ea8c9c_phase_b_current'])):.1f}" for i in range(3)), "A")
for t in ts:
    if t>=min(S['sensor.shellypro3em_ece334ea8c9c_phase_b_current']) and max(ex(t))>=4: print("   ", dt.datetime.fromtimestamp(t/1000).strftime('%a %m-%d %H:00'), " / ".join(f"{x:+.1f}" for x in ex(t)))
for t in ts:
    if t<min(S['sensor.shellypro3em_ece334ea8c9c_phase_b_current']) and max(ex(t))>=6: print("   pre-Sep", dt.datetime.fromtimestamp(t/1000).strftime('%a %m-%d %H:00'), " / ".join(f"{x:+.1f}" for x in ex(t)))
