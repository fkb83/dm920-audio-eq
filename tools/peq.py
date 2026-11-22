"""RBJ biquads -> dm920eq Q5.26 line. usage: peq.py type:f:gain:q ... ; prints coef line and expected response"""
import sys,math,cmath
FS=48000.
def rbj(t,f,g,q):
    A=10**(g/40.); w=2*math.pi*f/FS; c=math.cos(w); s=math.sin(w); al=s/(2*q)
    if t=='peak': b=[1+al*A,-2*c,1-al*A]; a=[1+al/A,-2*c,1-al/A]
    elif t=='lowshelf':
        sq=2*math.sqrt(A)*al
        b=[A*((A+1)-(A-1)*c+sq),2*A*((A-1)-(A+1)*c),A*((A+1)-(A-1)*c-sq)]
        a=[(A+1)+(A-1)*c+sq,-2*((A-1)+(A+1)*c),(A+1)+(A-1)*c-sq]
    elif t=='highshelf':
        sq=2*math.sqrt(A)*al
        b=[A*((A+1)+(A-1)*c+sq),-2*A*((A-1)+(A+1)*c),A*((A+1)+(A-1)*c-sq)]
        a=[(A+1)-(A-1)*c+sq,2*((A-1)-(A+1)*c),(A+1)-(A-1)*c-sq]
    elif t=='gain': return [10**(g/20.),0,0,0,0]
    else: raise SystemExit('bad type')
    return [b[0]/a[0],b[1]/a[0],b[2]/a[0],-a[1]/a[0],-a[2]/a[0]]   # hw: a added
def resp(bq,f):
    z=cmath.exp(-2j*math.pi*f/FS); h=1
    for b0,b1,b2,a1,a2 in bq: h*=(b0+b1*z+b2*z*z)/(1-a1*z-a2*z*z)
    return abs(h)
bq=[]
for arg in sys.argv[1:]:
    t,f,g,q=arg.split(':'); bq.append(rbj(t,float(f),float(g),float(q)))
peak=max(resp(bq,f) for f in [10*1.02**i for i in range(400)] if f<24000)
if peak>1.0001: sys.stderr.write('WARNING: max gain %.2f dB > 0 dB\n'%(20*math.log10(peak)))
print(len(bq),' '.join(str(int(round(c*(1<<26)))) for q5 in bq for c in q5))
sys.stderr.write('expected: '+'  '.join('%5dHz:%6.1fdB'%(f,20*math.log10(resp(bq,f))) for f in [50,100,200,500,1000,2000,5000,10000,15000,20000])+'\n')
sys.stderr.write('max gain: %.2f dB\n'%(20*math.log10(peak)))
