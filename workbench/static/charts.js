export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const numeric = x => typeof x === 'number' && Number.isFinite(x);
const label = x => x === null || x === undefined ? 'null' : typeof x === 'object' ? JSON.stringify(x) : String(x);
export function tableMarkup(rows, limit=80) {
  if (!rows?.length) return '<p class="muted small">데이터가 없습니다.</p>';
  const keys = [...new Set(rows.flatMap(r => Object.keys(r)))];
  return `<div class="table-scroll"><table class="metric-table"><thead><tr>${keys.map(k=>`<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,limit).map(r=>`<tr>${keys.map(k=>`<td>${esc(label(r[k]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${rows.length>limit?`<p class="muted small">${limit} / ${rows.length}행 미리보기. 전체 데이터는 결과 JSON에 있습니다.</p>`:''}`;
}
export function chartMarkup(spec, rows) {
  if(spec.kind==='table') return tableMarkup(rows);
  const ys=spec.y, fields=[...ys,...[spec.lower,spec.upper].filter(Boolean)];
  const values=rows.flatMap(r=>fields.map(k=>r[k]).filter(numeric));
  if(!rows.length||!values.length) return '<p class="note">추가 입력이 필요하거나 수치가 null입니다. 0으로 대체하지 않습니다.</p>';
  const W=660,H=280,L=72,R=24,T=18,B=53;
  let lo=Math.min(...values),hi=Math.max(...values);
  if(spec.unit==='probability'){lo=0;hi=1;}
  else {if(spec.kind==='bar'){lo=Math.min(lo,0);hi=Math.max(hi,0);}if(lo===hi){lo-=1;hi+=1;}const pad=(hi-lo)*.1;lo-=pad;hi+=pad;}
  const y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
  const x=i=>spec.kind==='bar'?L+(i+.5)/rows.length*(W-L-R):L+i/Math.max(1,rows.length-1)*(W-L-R);
  const money=v=>Math.abs(v)>=1e8?`${(v/1e8).toFixed(1)}억`:Math.abs(v)>=1e4?`${(v/1e4).toFixed(0)}만`:v.toFixed(0);
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title)}">`;
  for(let i=0;i<=4;i++){let v=lo+(hi-lo)*i/4; s+=`<path d="M${L} ${y(v)}H${W-R}" stroke="currentColor" opacity=".12"/><text x="${L-9}" y="${y(v)+4}" text-anchor="end">${esc(spec.unit==='probability'?`${Math.round(v*100)}%`:money(v))}</text>`;}
  if(spec.kind==='scatter'){
    const xv=rows.map(r=>r[spec.x]).filter(numeric);let xmin=Math.min(...xv),xmax=Math.max(...xv);if(xmin===xmax)xmax=xmin+1;
    const xx=v=>L+(v-xmin)/(xmax-xmin)*(W-L-R);
    for(let i=0;i<=4;i++){let v=xmin+(xmax-xmin)*i/4;s+=`<text x="${xx(v)}" y="${H-24}" text-anchor="middle">${money(v)}</text>`;}
    for(const r of rows){if(!numeric(r[spec.x])||!numeric(r[ys[0]]))continue;s+=`<circle cx="${xx(r[spec.x])}" cy="${y(r[ys[0]])}" r="${r.selected?7:4}" fill="currentColor" opacity="${r.feasible?1:.28}"><title>${esc(JSON.stringify(r))}</title></circle>`;}
  } else {
    const indices=[...new Set(Array.from({length:Math.min(rows.length,6)},(_,i)=>Math.round(i*(rows.length-1)/Math.max(1,Math.min(rows.length,6)-1))))];
    for(const i of indices){let t=String(rows[i][spec.x]);if(/^\d{4}-\d{2}-\d{2}$/.test(t))t=t.slice(5);if(t.length>8)t=t.slice(0,7)+'…';s+=`<text x="${x(i)}" y="${H-24}" text-anchor="middle">${esc(t)}</text>`;}
    if(spec.lower && spec.upper){
      let segments=[],segment=[];
      rows.forEach((r,i)=>{if(numeric(r[spec.lower])&&numeric(r[spec.upper]))segment.push([i,r]);else if(segment.length){segments.push(segment);segment=[];}});if(segment.length)segments.push(segment);
      for(const seg of segments){const points=seg.map(([i,r])=>`${x(i)},${y(r[spec.upper])}`).concat([...seg].reverse().map(([i,r])=>`${x(i)},${y(r[spec.lower])}`));s+=`<polygon points="${points.join(' ')}" fill="currentColor" opacity=".12"/>`;}
    }
    ys.forEach((k,j)=>{
      if(spec.kind==='bar'){
        const width=(W-L-R)/rows.length*.6/ys.length;
        rows.forEach((r,i)=>{if(!numeric(r[k]))return;let h=Math.abs(y(r[k])-y(0));s+=`<rect x="${x(i)-width*ys.length/2+j*width}" y="${Math.min(y(r[k]),y(0))}" width="${width}" height="${h}" fill="currentColor" opacity="${.8-j*.15}"><title>${esc(label(rows[i][spec.x]))}: ${esc(r[k])}</title></rect>`;});
      } else {
        let path='',connected=false;
        rows.forEach((r,i)=>{if(!numeric(r[k])){connected=false;return;}path+=`${connected?'L':'M'}${x(i)} ${y(r[k])} `;connected=true;});
        s+=`<path d="${path}" fill="none" stroke="currentColor" stroke-width="2" ${j%2?'stroke-dasharray="6 4" opacity=".65"':''}/>`;
      }
    });
  }
  s+='</svg>';
  const legend=spec.kind==='scatter'?'x: 절감액(원) · y: 목표·무부족 동시 확률 · 진한 점: 조건 충족 · 큰 점: 선택':ys.map((k,j)=>`${j%2?'점선':'실선'}: ${k}`).join(' / ');
  return s+`<p class="chart-legend">${esc(legend)}${spec.lower?' · 음영: P10~P90':''}</p>`;
}
