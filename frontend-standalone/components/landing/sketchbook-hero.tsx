"use client";

import Link from "next/link";
import { ArrowLeft, ArrowRight } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";

const destinations = [
  { file:"西湖.png", name:"杭州西湖", place:"湖光与慢行" }, { file:"苏州园林.png", name:"苏州园林", place:"移步换景" },
  { file:"故宫.png", name:"北京故宫", place:"中轴与宫墙" }, { file:"上海.png", name:"上海", place:"海派街巷" },
  { file:"万里长城.png", name:"万里长城", place:"沿山脊远行" }, { file:"漓江.png", name:"桂林漓江", place:"山水之间" },
  { file:"张家界.png", name:"张家界", place:"云雾峰林" }, { file:"九寨沟.png", name:"九寨沟", place:"海子与彩林" },
  { file:"布达拉宫.png", name:"拉萨", place:"高原的光" }, { file:"月牙泉.png", name:"敦煌", place:"沙丘清泉" },
];

type Turn = { dir:"next"|"prev"; from:number; to:number; t:number };

export function SketchbookHero() {
  const rootRef=useRef<HTMLDivElement>(null);

  useEffect(()=>{
    const root=rootRef.current;if(!root)return;
    const book=root.querySelector<HTMLDivElement>(".sbx-book");
    const stage=root.querySelector<HTMLDivElement>(".sbx-stage");
    const tilt=root.querySelector<HTMLDivElement>(".sbx-tilt");
    const caption=root.querySelector<HTMLParagraphElement>(".sbx-caption");
    const hint=root.querySelector<HTMLParagraphElement>(".sbx-hint");
    const prev=root.querySelector<HTMLButtonElement>(".sbx-arrow.left");
    const next=root.querySelector<HTMLButtonElement>(".sbx-arrow.right");
    if(!book||!stage||!tilt||!caption||!hint||!prev||!next)return;
    const reduced=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const compact=window.matchMedia("(max-width: 720px)").matches;
    const lowPower=(navigator.hardwareConcurrency ?? 8) <= 4;
    const N=compact?10:lowPower?12:16,SPAN=.449,BETA=.54,M=destinations.length;
    let index=0,turn:Turn|null=null,strips:HTMLDivElement[]=[],raf=0,inputRaf=0,tiltRaf=0,lastFrame=0,bookRect=book.getBoundingClientRect(),drag:null|{dir:"next"|"prev";x0:number;w:number;moved:number;lastT:number;lastAt:number;velocity:number}=null;
    let spring:null|{target:number;velocity:number;done:()=>void}=null;
    const imageCache=new Map<number,HTMLImageElement>();
    function make<K extends keyof HTMLElementTagNameMap>(tag:K,className:string):HTMLElementTagNameMap[K] { const element=document.createElement(tag);element.className=className;return element; }
    const imageUrl=(value:number)=>`/destinations/${destinations[value].file}`;
    const preload=(value:number)=>{if(imageCache.has(value))return;const image=new Image();image.decoding="async";image.src=imageUrl(value);imageCache.set(value,image);void image.decode().catch(()=>undefined);};
    const half=(side:"left"|"right",value:number)=>{const shell=make("div",`sbx-half ${side}`);const image=document.createElement("img");image.src=imageUrl(value);image.alt="";image.draggable=false;image.className=`sbx-half-img ${side}`;shell.append(image,make("div",`sbx-gutter ${side}`));return shell;};
    const buildCurl=(dir:"next"|"prev",from:number,to:number)=>{strips=[];const curl=make("div",`sbx-curl ${dir}`);curl.style.setProperty("--n",String(N));curl.style.setProperty("--span",String(SPAN));let host:HTMLElement=curl;for(let i=0;i<N;i++){const strip=make("div","sbx-strip"),depth=Math.sin(((i+.5)/N)*Math.PI);strip.style.setProperty("--strip-dark",(.08+depth*.30).toFixed(3));strip.style.setProperty("--strip-glint",(.02+(1-depth)*.10).toFixed(3));const gut="calc(var(--bw) * .5)",sw=`calc(var(--bw) * ${SPAN} / ${N})`;const A=`calc(-1 * (${gut} + ${i} * ${sw}))`,B=`calc(${i+1} * ${sw} - ${gut})`;const front=make("div","sbx-face front"),back=make("div","sbx-face back");front.style.backgroundImage=`url('${imageUrl(from)}')`;back.style.backgroundImage=`url('${imageUrl(to)}')`;front.style.backgroundPositionX=dir==="next"?A:B;back.style.backgroundPositionX=dir==="next"?B:A;strip.append(front,back);if(i===N-1)strip.classList.add("edge");host.append(strip);host=strip;strips.push(strip);}return curl;};
    const apply=(value:number)=>{if(!turn)return;const theta=Math.PI*value,beta=BETA*Math.sin(Math.PI*value),deg=180/Math.PI,total=theta+beta,delta=2*beta/N;root.style.setProperty("--tt",`${(total*deg).toFixed(2)}deg`);root.style.setProperty("--td",`${(delta*deg).toFixed(3)}deg`);root.style.setProperty("--shade",Math.sin(Math.PI*value).toFixed(3));};
    const queueApply=(value:number)=>{if(inputRaf)cancelAnimationFrame(inputRaf);inputRaf=requestAnimationFrame(()=>{inputRaf=0;apply(value);});};
    const mark=()=>root.querySelectorAll<HTMLButtonElement>(".sbx-index-button").forEach((button,i)=>button.setAttribute("aria-current",i===index?"true":"false"));
    const paint=()=>{book.replaceChildren();if(!turn){const full=make("div","sbx-full");const image=document.createElement("img");image.src=imageUrl(index);image.alt=destinations[index].name;image.draggable=false;image.decoding="async";full.append(image);book.append(full);root.style.setProperty("--shade","0");caption.textContent=`${destinations[index].name} · ${destinations[index].place}`;preload((index+1)%M);preload((index-1+M)%M);}else{const forward=turn.dir==="next";book.append(half("left",forward?turn.from:turn.to),half("right",forward?turn.to:turn.from),buildCurl(turn.dir,turn.from,turn.to));caption.textContent=`${destinations[turn.to].name} · ${destinations[turn.to].place}`;apply(turn.t);}const left=make("button","sbx-zone prev"),right=make("button","sbx-zone next");left.setAttribute("aria-label","上一页");right.setAttribute("aria-label","下一页");book.append(left,right);bookRect=book.getBoundingClientRect();root.style.setProperty("--bw",`${bookRect.width}px`);mark();};
    const loop=(now:number)=>{const dt=lastFrame?Math.min(.032,(now-lastFrame)/1000):.016;lastFrame=now;if(spring&&turn){const x=turn.t-spring.target;spring.velocity+=(-180*x-27*spring.velocity)*dt;turn.t+=spring.velocity*dt;if(Math.abs(turn.t-spring.target)<.002&&Math.abs(spring.velocity)<.02){turn.t=spring.target;apply(turn.t);const done=spring.done;spring=null;done();}else apply(turn.t);}if(spring){raf=requestAnimationFrame(loop)}else{raf=0;lastFrame=0;}};
    const animateTo=(target:number,done:()=>void,velocity=0)=>{if(reduced){if(turn)turn.t=target;done();return;}spring={target,velocity,done};lastFrame=0;if(!raf)raf=requestAnimationFrame(loop);};
    const start=(dir:"next"|"prev",value=0)=>{if(turn){index=turn.to;turn=null;}const from=index,to=dir==="next"?(from+1)%M:(from-1+M)%M;preload(to);turn={dir,from,to,t:value};paint();};
    const commit=(velocity=0)=>{if(!turn)return;animateTo(1,()=>{if(!turn)return;index=turn.to;turn=null;paint();},Math.max(-3,Math.min(3,velocity)));};
    const cancel=(velocity=0)=>animateTo(0,()=>{turn=null;paint();},Math.max(-3,Math.min(3,velocity)));
    const step=(dir:"next"|"prev")=>{if(spring||drag)return;hint.classList.add("gone");start(dir);commit();};
    const go=(target:number)=>{if(target===index)return;const fwd=(target-index+M)%M,back=(index-target+M)%M;if(Math.min(fwd,back)===1)step(fwd===1?"next":"prev");else{index=target;turn=null;paint();}};
    const down=(event:PointerEvent)=>{if(event.button!==0||spring)return;const zone=(event.target as HTMLElement).closest(".sbx-zone");if(!zone)return;event.preventDefault();stage.setPointerCapture(event.pointerId);const rect=bookRect,dir=(event.clientX-rect.left)/rect.width>.5?"next":"prev";start(dir);drag={dir,x0:event.clientX,w:rect.width,moved:0,lastT:0,lastAt:performance.now(),velocity:0};};
    const move=(event:PointerEvent)=>{if(!drag||!turn)return;const dx=event.clientX-drag.x0;drag.moved=Math.max(drag.moved,Math.abs(dx));const value=Math.max(0,Math.min(1,(drag.dir==="next"?-dx:dx)/(drag.w*.62))),now=performance.now();drag.velocity=(value-drag.lastT)/Math.max(.001,(now-drag.lastAt)/1000);drag.lastT=value;drag.lastAt=now;turn.t=value;queueApply(value);};
    const up=()=>{if(!drag||!turn)return;const current=drag;drag=null;if(current.moved<6||turn.t>.42||current.velocity>1.1)commit(current.velocity);else cancel(current.velocity);};
    const pointerTilt=(event:PointerEvent)=>{if(event.pointerType==="touch"||drag)return;const x=Math.max(-1,Math.min(1,(event.clientX-(bookRect.left+bookRect.width/2))/(bookRect.width*.62))),y=Math.max(-1,Math.min(1,(event.clientY-(bookRect.top+bookRect.height/2))/(bookRect.height*.9)));if(tiltRaf)cancelAnimationFrame(tiltRaf);tiltRaf=requestAnimationFrame(()=>{tiltRaf=0;tilt.style.setProperty("--rx",`${(-y*4.5).toFixed(2)}deg`);tilt.style.setProperty("--ry",`${(x*7).toFixed(2)}deg`);});};
    const key=(event:KeyboardEvent)=>{if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();step(event.key==="ArrowRight"?"next":"prev");}};
    const resize=()=>{bookRect=book.getBoundingClientRect();root.style.setProperty("--bw",`${bookRect.width}px`);};
    prev.onclick=()=>step("prev");next.onclick=()=>step("next");stage.addEventListener("pointerdown",down);stage.addEventListener("pointermove",move);stage.addEventListener("pointerup",up);stage.addEventListener("pointercancel",up);window.addEventListener("pointermove",pointerTilt,{passive:true});window.addEventListener("keydown",key);window.addEventListener("resize",resize);
    root.querySelectorAll<HTMLButtonElement>(".sbx-index-button").forEach((button,i)=>button.onclick=()=>{go(i);root.scrollIntoView({behavior:reduced?"auto":"smooth",block:"start"});});
    paint();
    const intro=window.setTimeout(()=>{if(!reduced)step("next");},420);
    return()=>{window.clearTimeout(intro);if(raf)cancelAnimationFrame(raf);if(inputRaf)cancelAnimationFrame(inputRaf);if(tiltRaf)cancelAnimationFrame(tiltRaf);stage.removeEventListener("pointerdown",down);stage.removeEventListener("pointermove",move);stage.removeEventListener("pointerup",up);stage.removeEventListener("pointercancel",up);window.removeEventListener("pointermove",pointerTilt);window.removeEventListener("keydown",key);window.removeEventListener("resize",resize);};
  },[]);

  return <div ref={rootRef} className="sketchbook-home">
    <header className="sketchbook-top"><Link href="/" className="sketchbook-name">行迹</Link><nav><Link href="/create">开始规划</Link><Link href="/trip/demo-trip">示例行程</Link><Link href="/trips">我的行程</Link></nav></header>
    <section className="sketchbook-hero"><span aria-hidden="true" className="sketch-sprite sprite-travel decor-willow"/><span aria-hidden="true" className="sketch-sprite sprite-travel decor-plum"/><p className="sketchbook-kicker">把中国的山水，翻进你的下一段旅程</p><div className="sbx-wrap"><div className="sbx-stage"><button className="sbx-arrow left" aria-label="上一页"><ArrowLeft size={26}/></button><div className="sbx-3d"><div className="sbx-tilt"><div className="sbx-shadow ambient"/><div className="sbx-shadow contact"/><div className="sbx-book"/></div></div><button className="sbx-arrow right" aria-label="下一页"><ArrowRight size={26}/></button></div><p className="sbx-caption"/><p className="sbx-hint">拖动纸页翻阅 · 点击两侧继续</p><Link className="sketchbook-cta" href="/create">开始规划我的旅程</Link></div></section>
    <section className="sketchbook-about"><span aria-hidden="true" className="sketch-sprite sprite-travel decor-cloud"/><span aria-hidden="true" className="sketch-sprite sprite-soft decor-flower"/><p>关于行迹</p><div className="sketchbook-about-copy"><h1>旅行从来不只是抵达某个地方，而是把喜欢的风景、合适的节奏和一路上的期待，慢慢连成一条属于自己的路。</h1><p><strong>行迹，从一句想法开始。</strong>让每一天的停留、转身与出发，都有迹可循，也留有余地。</p></div></section>
    <section className="sketchbook-index"><span aria-hidden="true" className="sketch-sprite sprite-soft decor-grass"/><span aria-hidden="true" className="sketch-sprite sprite-soft decor-round-leaf"/><p>目的地手账</p><ol>{destinations.map((item,index)=><li key={item.file}><button className="sbx-index-button"><span>{String(index+1).padStart(2,"0")}</span><b>{item.name}</b><small>{item.place}</small></button></li>)}</ol></section>
    <footer className="sketchbook-footer"><span aria-hidden="true" className="sketch-sprite sprite-soft decor-clover"/><p>准备好翻开下一页了吗？</p><Link href="/create">创建一段旅程</Link></footer>
  </div>;
}
