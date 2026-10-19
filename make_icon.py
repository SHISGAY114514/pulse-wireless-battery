"""Generate the small native PULSE application icon using the original ICO writer."""
from pathlib import Path
from win_icon import build_ico

def inside(x,y,points):
    result=False
    j=len(points)-1
    for i,(px,py) in enumerate(points):
        qx,qy=points[j]
        if ((py>y)!=(qy>y)) and x<(qx-px)*(y-py)/(qy-py)+px:
            result=not result
        j=i
    return result

frames=[]
for size in (16,32,48,64,128):
    pixels=bytearray()
    for y in range(size):
        for x in range(size):
            px,py=x/size,y/size
            rounded=min(px,1-px)+min(py,1-py)>.1
            bolt=inside(px,py,[(.54,.16),(.29,.53),(.47,.53),(.40,.85),(.74,.41),(.55,.41)])
            pixels.extend((188,236,216,255) if bolt else (23,47,56,255) if rounded else (0,0,0,0))
    frames.append((size,bytes(pixels)))
Path(__file__).with_name('pulse.ico').write_bytes(build_ico(frames))
