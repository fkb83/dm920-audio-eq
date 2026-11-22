import sys,struct,math
d=open(sys.argv[1],'rb').read()
la,lb=struct.unpack_from('<II',d,0); info=struct.unpack_from('<8I',d,8)
CAP=96*1024; off=40
print('len',la,lb,'infoA',info[:4],'infoB',info[4:])
def samples(i,n):
    return struct.unpack_from('<%di'%(n//4),d,off+i*CAP)
A=samples(0,la); B=samples(1,lb)
def rms(x): return math.sqrt(sum(v*v for v in x)/max(1,len(x)))
for name,x in (('pre',A),('post',B)):
    L=x[0::2]; R=x[1::2]
    print(name,'rmsL %.0f rmsR %.0f peak %d'%(rms(L),rms(R),max(abs(v) for v in x) if x else 0))
ra,rb=rms(A),rms(B)
if ra: print('ratio post/pre = %.4f  (%.2f dB)'%(rb/ra, 20*math.log10(rb/ra) if rb else -999))
