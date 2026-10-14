"""Independent PC2 geometry validation helpers; never modify solver output."""
import numpy as np

def segment_pierces_triangle(start, end, triangle, epsilon=1e-8):
    """Strict interior crossing; boundary contact is not penetration."""
    a,b,c=np.asarray(triangle,dtype=float)
    direction=np.asarray(end)-start
    e1=b-a;e2=c-a
    h=np.cross(direction,e2);det=float(np.dot(e1,h))
    if abs(det)<epsilon:return False
    s=np.asarray(start)-a
    u=float(np.dot(s,h)/det)
    q=np.cross(s,e1)
    v=float(np.dot(direction,q)/det)
    t=float(np.dot(e2,q)/det)
    return epsilon<u and epsilon<v and u+v<1-epsilon and epsilon<t<1-epsilon

def triangles_pierce(a,b):
    return any(segment_pierces_triangle(t[i],t[(i+1)%3],other)
               for t,other in ((a,b),(b,a)) for i in range(3))

def acceleration_max(samples,fps):
    x=np.asarray(samples)
    if len(x)<3:return 0.
    return float(np.linalg.norm(np.diff(x,n=2,axis=0)*fps**2,axis=2).max())
