import sys,struct,numpy as np
d=open(sys.argv[1],'rb').read()
la,lb=struct.unpack_from('<II',d,0); CAP=96*1024; off=40
A=np.frombuffer(d[off:off+la],dtype='<i4').astype(float).reshape(-1,2).mean(1)
B=np.frombuffer(d[off+CAP:off+CAP+lb],dtype='<i4').astype(float).reshape(-1,2).mean(1)
n=min(len(A),len(B)); A=A[:n]; B=B[:n]
# align via cross-correlation (lag search +-2000)
best=(0,-1)
for lag in range(-2000,2001,1):
    a=A[max(0,lag):n+min(0,lag)]; b=B[max(0,-lag):n-max(0,lag)]
    c=np.dot(a,b)/np.sqrt(np.dot(a,a)*np.dot(b,b)+1e-9)
    if abs(c)>best[1]: best=(lag,abs(c))
lag=best[0]; a=A[max(0,lag):n+min(0,lag)]; b=B[max(0,-lag):n-max(0,lag)]
N=2048; w=np.hanning(N); Sxx=0; Sxy=0
for i in range(0,len(a)-N,N//2):
    X=np.fft.rfft(a[i:i+N]*w); Y=np.fft.rfft(b[i:i+N]*w); Sxx=Sxx+abs(X)**2; Sxy=Sxy+Y*np.conj(X)
H=Sxy/(Sxx+1e-9); f=np.fft.rfftfreq(N,1/48000.)
print('lag %d corr %.3f  samples %d'%(lag,best[1],len(a)))
out=[]
for fc in [50,100,200,500,1000,2000,5000,10000,15000,20000]:
    k=np.argmin(abs(f-fc)); sel=slice(max(1,k-2),k+3)
    out.append('%5dHz:%6.1fdB'%(fc,20*np.log10(abs(H[sel]).mean()+1e-12)))
print('  '.join(out))
