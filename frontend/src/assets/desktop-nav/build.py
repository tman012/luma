# Static mockup compositor: original Luma dock icons (Pillow, 1024px masters) + frosted dock.
from PIL import Image,ImageDraw,ImageFilter,ImageChops
import numpy as np,cv2,math
from pathlib import Path
O=Path(__file__).parent; SRC='/workspace/luma-qa/fastgate/ece4395c/designer/home-probe.png'
N=1024
def grad(size,c1,c2,angle=90):
    w,h=size; y,x=np.mgrid[0:h,0:w].astype(float)
    a=math.radians(angle); t=(x/w*math.cos(a)+y/h*math.sin(a)); t=(t-t.min())/(t.max()-t.min())
    c1=np.array(c1,float);c2=np.array(c2,float)
    arr=c1[None,None,:]*(1-t[...,None])+c2[None,None,:]*t[...,None]
    return Image.fromarray(arr.astype(np.uint8),'RGBA' if len(c1)==4 else 'RGB').convert('RGBA')
def rgrad(size,c1,c2,center,rad):
    w,h=size;y,x=np.mgrid[0:h,0:w].astype(float)
    t=np.clip(np.hypot(x-center[0],y-center[1])/rad,0,1)
    c1=np.array(c1,float);c2=np.array(c2,float)
    return Image.fromarray((c1*(1-t[...,None])+c2*t[...,None]).astype(np.uint8),'RGBA')
def mask(draw_fn):
    m=Image.new('L',(N,N),0);draw_fn(ImageDraw.Draw(m));return m
def paste(base,fill,m):
    f=fill if isinstance(fill,Image.Image) else Image.new('RGBA',(N,N),fill)
    base.paste(f,(0,0),ImageChops.multiply(m,f.getchannel('A')))
def shadow(base,m,off=(0,24),blur=26,alpha=110):
    s=Image.new('RGBA',(N,N),(0,0,0,0));sm=m.filter(ImageFilter.GaussianBlur(blur)).point(lambda v:v*alpha//255)
    s.putalpha(ImageChops.offset(sm,*off));base.alpha_composite(s)
def gloss(base,m,strength=90):
    # top specular sheen clipped to shape
    g=grad((N,N),(255,255,255,strength),(255,255,255,0),90)
    g.putalpha(ImageChops.multiply(g.getchannel('A'),m));base.alpha_composite(g)
SQ=mask(lambda d:d.rounded_rectangle((40,40,984,984),radius=225,fill=255))
def tile(c1,c2):
    t=Image.new('RGBA',(N,N),(0,0,0,0));paste(t,grad((N,N),c1,c2,100),SQ)
    # bevel: inner light rim top, dark rim bottom
    rim=mask(lambda d:d.rounded_rectangle((40,40,984,984),radius=225,outline=255,width=10)).filter(ImageFilter.GaussianBlur(3))
    paste(t,grad((N,N),(255,255,255,150),(0,0,0,90),90),rim)
    top=mask(lambda d:d.ellipse((-200,-760,1224,470),fill=255)).filter(ImageFilter.GaussianBlur(30))
    paste(t,(255,255,255,38),ImageChops.multiply(top,SQ))
    return t
def search():
    t=tile((70,170,255,255),(20,60,190,255))
    ring=mask(lambda d:(d.ellipse((230,200,700,670),fill=255),d.ellipse((300,270,630,600),fill=0)))
    handle=mask(lambda d:d.line([(620,600),(800,790)],fill=255,width=120))
    hc=mask(lambda d:(d.line([(620,600),(800,790)],fill=255,width=120),d.ellipse((740,730,860,850),fill=255)))
    lens=mask(lambda d:d.ellipse((300,270,630,600),fill=255))
    shadow(t,ImageChops.lighter(ring,hc))
    paste(t,grad((N,N),(150,95,40,255),(70,40,15,255),45),hc); gloss(t,hc,70)
    paste(t,rgrad((N,N),(220,250,255,150),(120,200,255,90),(430,400),260),lens)
    hl=mask(lambda d:d.chord((330,295,600,560),200,300,fill=255)).filter(ImageFilter.GaussianBlur(8));paste(t,(255,255,255,170),hl)
    paste(t,grad((N,N),(250,252,255,255),(170,185,205,255),90),ring)
    inner=mask(lambda d:d.ellipse((300,270,630,600),outline=255,width=12));paste(t,(60,70,90,160),inner)
    return t
def library():
    t=tile((150,90,250,255),(70,40,170,255))
    cols=[((90,200,255,255),(30,90,200,255),-14),((255,140,190,255),(190,50,120,255),-5),((255,215,120,255),(230,120,40,255),4)]
    for i,(a,b,ang) in enumerate(cols):
        L=Image.new('L',(N,N),0);ImageDraw.Draw(L).rounded_rectangle((300,250,700,800),radius=40,fill=255)
        L=L.rotate(ang,center=(500,800),resample=Image.BICUBIC,translate=(i*45-40,-i*20))
        shadow(t,L,(0,18),20,120); paste(t,grad((N,N),a,b,110),L)
        if i==2:
            fr=Image.new('L',(N,N),0);ImageDraw.Draw(fr).rounded_rectangle((350,310,650,560),radius=24,fill=255)
            fr=fr.rotate(ang,center=(500,800),resample=Image.BICUBIC,translate=(i*45-40,-i*20))
            paste(t,grad((N,N),(255,250,235,235),(255,200,140,200),90),fr)
            tri=Image.new('L',(N,N),0);ImageDraw.Draw(tri).polygon([(465,380),(465,490),(555,435)],fill=255)
            tri=tri.rotate(ang,center=(500,800),resample=Image.BICUBIC,translate=(i*45-40,-i*20));paste(t,(200,90,40,255),tri)
        gloss(t,L,60)
    return t
def explore():
    t=tile((60,215,200,255),(10,110,120,255))
    face=mask(lambda d:d.ellipse((222,222,802,802),fill=255)); shadow(t,face)
    paste(t,grad((N,N),(245,250,250,255),(190,205,210,255),90),face)
    paste(t,rgrad((N,N),(230,250,248,255),(160,215,212,255),(512,470),300),mask(lambda d:d.ellipse((262,262,762,762),fill=255)))
    tk=mask(lambda d:[d.line([(512+math.cos(a)*230,512+math.sin(a)*230),(512+math.cos(a)*(200 if k%3 else 180),512+math.sin(a)*(200 if k%3 else 180))],fill=255,width=10 if k%3 else 16) for k,a in enumerate([i*math.pi/12 for i in range(24)])])
    paste(t,(40,90,95,200),tk)
    r=math.radians(45);c=512
    def pt(d,a):return (c+math.cos(a)*d,c+math.sin(a)*d)
    n=mask(lambda d:d.polygon([pt(210,-r-math.pi/2+math.pi/2*0-0.0+ -math.pi/4+math.pi/4),pt(48,-r+0.0),pt(48,-r-math.pi)],fill=255))
    a0=-math.pi/4-math.pi/2+math.pi/2  # northeast
    nd=mask(lambda d:d.polygon([pt(220,-math.pi/4),pt(52,math.pi/4),pt(52,-3*math.pi/4)],fill=255))
    sd=mask(lambda d:d.polygon([pt(220,3*math.pi/4),pt(52,math.pi/4),pt(52,-3*math.pi/4)],fill=255))
    shadow(t,ImageChops.lighter(nd,sd),(0,14),12,120)
    paste(t,grad((N,N),(255,110,90,255),(210,40,40,255),0),nd);paste(t,grad((N,N),(90,110,120,255),(40,55,65,255),0),sd)
    half=mask(lambda d:d.polygon([pt(220,-math.pi/4),pt(220,3*math.pi/4),pt(52,-3*math.pi/4)],fill=255))
    paste(t,(255,255,255,55),ImageChops.multiply(half,ImageChops.lighter(nd,sd)))
    paste(t,grad((N,N),(255,255,255,255),(180,180,190,255),90),mask(lambda d:d.ellipse((482,482,542,542),fill=255)))
    gloss(t,mask(lambda d:d.ellipse((262,262,762,560),fill=255)).filter(ImageFilter.GaussianBlur(20)),80)
    return t
def calendar():
    t=tile((255,250,240,255),(225,215,200,255))
    pg=mask(lambda d:d.rounded_rectangle((220,250,804,820),radius=60,fill=255)); shadow(t,pg,(0,20),22,80)
    paste(t,grad((N,N),(255,255,255,255),(238,232,222,255),90),pg)
    band=ImageChops.multiply(pg,mask(lambda d:d.rectangle((0,0,N,420),fill=255)))
    paste(t,grad((N,N),(255,125,100,255),(225,70,60,255),90),band); gloss(t,band,70)
    for x in (360,664):
        r=mask(lambda d,x=x:d.rounded_rectangle((x-26,190,x+26,330),radius=26,fill=255));shadow(t,r,(0,8),6,120)
        paste(t,grad((N,N),(250,250,252,255),(150,155,165,255),0),r)
    for row in range(3):
        for col in range(5):
            x=285+col*94;y=475+row*102
            m=mask(lambda d,x=x,y=y:d.rounded_rectangle((x,y,x+64,y+64),radius=14,fill=255))
            paste(t,(255,110,90,255) if (row,col)==(1,2) else (205,195,185,255),m)
    return t
def watchlist():
    t=tile((255,200,90,255),(225,110,25,255))
    bm=mask(lambda d:d.polygon([(330,200),(694,200),(694,830),(512,690),(330,830)],fill=255))
    bm=bm.filter(ImageFilter.GaussianBlur(1)); shadow(t,bm,(0,24),24,120)
    paste(t,grad((N,N),(225,40,70,255),(140,15,45,255),0),bm)
    fold=ImageChops.multiply(bm,mask(lambda d:d.rectangle((512,0,N,N),fill=255)));paste(t,(0,0,0,45),fold)
    st=[(512+math.cos(-math.pi/2+i*math.pi/5)*(130 if i%2==0 else 55),430+math.sin(-math.pi/2+i*math.pi/5)*(130 if i%2==0 else 55)) for i in range(10)]
    s=mask(lambda d:d.polygon(st,fill=255));shadow(t,s,(0,8),6,100)
    paste(t,grad((N,N),(255,245,190,255),(250,190,60,255),90),s);gloss(t,bm,55)
    return t
ICONS=[('search',search),('libraries',library),('explore',explore),('calendar',calendar),('watchlist',watchlist)]
(O/'icons').mkdir(exist_ok=True); masters=[]
for n,f in ICONS:
    im=f(); im.putalpha(ImageChops.multiply(im.getchannel('A'),SQ.point(lambda v:255) if False else Image.new('L',(N,N),255)))
    im.save(O/'icons'/f'luma-dock-{n}-1024.png'); masters.append(im)
    for s in (48,56,144,224): im.resize((s,s),Image.LANCZOS).save(O/'icons'/f'luma-dock-{n}-{s}.png')
sheet=Image.new('RGBA',(5*300+60,360),(38,38,44,255))
for i,m in enumerate(masters): sheet.alpha_composite(m.resize((256,256),Image.LANCZOS),(52+i*300,52))
sheet.convert('RGB').save(O/'icons-contact-sheet.png')
# base: inpaint old chevron only
a=np.array(Image.open(SRC).convert('RGB'));mk=np.zeros(a.shape[:2],np.uint8);mk[43:61,707:733]=255
base=Image.fromarray(cv2.inpaint(a,mk,3,cv2.INPAINT_TELEA)).convert('RGBA'); base.save(O/'_base-chevron-removed.png')
def dock(scale,box,icon,gap,pad,radius):
    """render frosted dock + icons onto base at given scale; returns RGBA image W*scale"""
    W,H=base.size;big=base.resize((W*scale,H*scale),Image.LANCZOS) if scale>1 else base.copy()
    x0,y0,x1,y1=[v*scale for v in box];R=radius*scale
    dm=Image.new('L',big.size,0);ImageDraw.Draw(dm).rounded_rectangle((x0,y0,x1,y1),radius=R,fill=255)
    sh=Image.new('RGBA',big.size,(0,0,0,0));sh.putalpha(ImageChops.offset(dm,0,6*scale).filter(ImageFilter.GaussianBlur(14*scale)).point(lambda v:v*90//255));big.alpha_composite(sh)
    blur=big.filter(ImageFilter.GaussianBlur(18*scale))
    tint=Image.new('RGBA',big.size,(232,228,236,64));blur.alpha_composite(tint)
    tint2=Image.new('RGBA',big.size,(20,18,28,46));blur.alpha_composite(tint2)
    big.paste(blur,(0,0),dm)
    edge=Image.new('L',big.size,0);ImageDraw.Draw(edge).rounded_rectangle((x0,y0,x1,y1),radius=R,outline=255,width=max(1,scale))
    big.paste(Image.new('RGBA',big.size,(255,255,255,120)),(0,0),edge)
    n=len(masters);cx=x0+pad*scale;cy=(y0+y1)//2
    for m in masters:
        s=icon*scale;ic=m.resize((s,s),Image.LANCZOS)
        ish=Image.new('RGBA',(s+40*scale,s+40*scale),(0,0,0,0));al=Image.new('L',ish.size,0);al.paste(ic.getchannel('A'),(20*scale,20*scale))
        ish.putalpha(al.filter(ImageFilter.GaussianBlur(3*scale)).point(lambda v:v*80//255))
        big.alpha_composite(ish,(cx-20*scale,cy-s//2-20*scale+2*scale)); big.alpha_composite(ic,(cx,cy-s//2)); cx+=s+gap*scale
    return big
def width(icon,gap,pad):return 5*icon+4*gap+2*pad
specs={'C':dict(icon=48,gap=11,pad=12,h=72,radius=22),'D':dict(icon=56,gap=12,pad=14,h=84,radius=26)}
wC=width(48,11,12);boxC=(212,17,212+wC,17+72)          # beside real logo, top header
wD=width(56,12,14);boxD=(720-wD//2,806,720-wD//2+wD,806+84)  # bottom centre, below genres, right of description
for k,box in (('C',boxC),('D',boxD)):
    s=specs[k];args=(box,s['icon'],s['gap'],s['pad'],s['radius'])
    dock(1,*args).convert('RGB').save(O/f'{k}-full-context-1440x900.png')
    S=4;big=dock(S,*args);m=24;x0,y0,x1,y1=box
    big.crop(((x0-m-(150 if k=='C' else 0))*S,max(0,y0-m)*S,(x1+m)*S,min(900,y1+m)*S)).convert('RGB').save(O/f'{k}-closeup-4x.png')
print(boxC,boxD)
