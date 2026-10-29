const I18N=(()=>{
const EN=__DICT__;
let lang='vi';try{if(localStorage.getItem('lang')==='en')lang='en'}catch(e){}
const norm=s=>s.replace(/\s+/g,' ').trim();
// Luật cho chuỗi có tham số. Chuỗi gốc luôn là tiếng Việt; chưa khớp luật nào thì giữ nguyên.
const rules=[
 [/^(\d+) lượt hỏi$/,m=>`${m[1]} questions`],
 [/^(\d+) lượt trong 7 ngày$/,m=>`${m[1]} requests in 7 days`],
 [/^(\d+) tài khoản$/,m=>`${m[1]} accounts`],
 [/^(\d+)% đúng$/,m=>`${m[1]}% correct`],
 [/^(\d+) đã trả lời · (\d+) người dùng$/,m=>`${m[1]} answered · ${m[2]} users`],
 [/^(\d+) lượt bị chặn$/,m=>`${m[1]} blocked`],
 [/^(\d+) đúng · (\d+) sai$/,m=>`${m[1]} correct · ${m[2]} wrong`],
 [/^(.+): đã dùng (\S+)(?: \/ (\S+))? token hôm nay$/,m=>`${m[1]}: used ${m[2]}${m[3]?' / '+m[3]:''} tokens today`],
 [/^Chi phí đám mây: (.+) \/ (.+) USD$/,m=>`Cloud cost: ${m[1]} / ${m[2]} USD`],
 [/^Chi phí: (.+) USD$/,m=>`Cost: ${m[1]} USD`],
 [/^giữ đến (.*)$/,m=>`kept until ${m[1]}`],
 [/^(.+) GB · (\d+)% trên GPU$/,m=>`${m[1]} GB · ${m[2]}% on GPU`],
 [/^Còn trống (\d+) MiB$/,m=>`${m[1]} MiB free`],
 [/^Cập nhật (.+)$/,m=>`Updated ${m[1]}`],
 [/^(\S+): (\d+) lượt, (\d+) token$/,m=>`${m[1]}: ${m[2]} requests, ${m[3]} tokens`],
 [/^(.+) \(đám mây\)$/,m=>`${m[1]} (cloud)`],
 [/^Knowledge Base \((\d+)\): (.+)$/,m=>`Knowledge Base (${m[1]}): ${m[2]}`],
 [/^Đã (bật|tắt|khởi động lại) (.+)\.$/,m=>`${m[2]} ${m[1]==='bật'?'turned on':m[1]==='tắt'?'turned off':'restarted'}.`],
 [/^(.+): chưa thấy thay đổi, xem file logs\/(\w+)\.log$/,m=>`${m[1]}: no change detected, see logs/${m[2]}.log`],
 [/^Xóa tài liệu (.+) khỏi Knowledge Base\?$/,m=>`Delete document ${m[1]} from the Knowledge Base?`],
 [/^Xóa model (.+)\?$/,m=>`Delete model ${m[1]}?`],
 [/^Xóa tài khoản (.+) và toàn bộ lịch sử chat của người này\? Không thể khôi phục\.$/,m=>`Delete account ${m[1]} and all of this person's chat history? This cannot be undone.`],
 [/^Dòng không hợp lệ: (.+) \(cần dạng tên=số token\)$/,m=>`Invalid line: ${m[1]} (expected name=token count)`],
 [/^Đã nạp (.+) \((.+) ký tự\)\.$/,m=>`Loaded ${m[1]} (${m[2]} characters).`],
 [/^Đã xóa model (.+)\.$/,m=>`Model ${m[1]} deleted.`],
 [/^Đã xóa tài khoản (.+) và lịch sử chat của người đó\.$/,m=>`Account ${m[1]} and that person's chat history were deleted.`],
 [/^Đã xóa (.+)\.$/,m=>`${m[1]} deleted.`],
 [/^Đã tạo tài khoản (.+)\.$/,m=>`Account ${m[1]} created.`],
 [/^Đã lưu model (.+)\.$/,m=>`Model ${m[1]} saved.`],
 [/^(?:Đăng nhập sai quá nhiều lần|Sai quá nhiều lần)\. Thử lại sau (\d+) giây\.$/,m=>`Too many failed attempts. Try again in ${m[1]} seconds.`],
 [/^Không bật được (.+?): (.+)$/,m=>`Could not start ${m[1]}: ${m[2]}`],
 [/^Lỗi khi gọi model: (.+)$/,m=>`Error calling the model: ${m[1]}`],
 [/^Lỗi nội bộ: (.+)$/,m=>`Internal error: ${m[1]}`],
 [/^Lỗi: (.+)$/,m=>`Error: ${m[1]}`],
 [/^Không đọc được GPU: (.+)$/,m=>`Could not read the GPU: ${m[1]}`],
 [/^Không xác định được tiến trình đang giữ cổng (\d+)\. Hãy tắt thủ công\.$/,m=>`Could not identify the process holding port ${m[1]}. Please stop it manually.`],
 [/^Đã gửi lệnh tắt nhưng cổng vẫn mở(?: \(Ollama có thể đã tự khởi động lại từ khay hệ thống\))?\.$/,m=>`The stop command was sent but the port is still open${/Ollama/.test(m[0])?' (Ollama may have restarted itself from the system tray)':''}.`],
 [/^(.+) không do bảng điều khiển bật\. Tắt tiến trình đang giữ cổng (\d+): (.+?)( rồi bật lại)?\?( Ollama chạy từ khay hệ thống có thể tự bật lại; muốn tắt hẳn hãy chọn Quit ở khay\.)?$/,
   m=>`${m[1]} was not started by this dashboard. Stop the process holding port ${m[2]}: ${m[3]}${m[4]?' and start it again':''}?${m[5]?' Ollama running from the system tray may restart itself; to stop it completely choose Quit in the tray.':''}`],
 [/^Đã chuẩn bị (\d+) đoạn tài liệu\. Đang gửi prompt tới (.+) và chờ model sinh câu trả lời…$/,m=>`Prepared ${m[1]} document passages. Sending the prompt to ${m[2]} and waiting for the answer…`],
 [/^Câu hỏi bị chặn: (.+)\.$/,m=>`Question blocked: ${tr(m[1])}.`],
 [/^hết hạn mức (\d+) token hôm nay$/,m=>`daily limit of ${m[1]} tokens reached`],
 [/^bị chặn: (.+)$/,m=>`blocked: ${tr(m[1])}`,1],
 [/^bị từ chối: (.+)$/,m=>`rejected: ${tr(m[1])}`],
 [/^lỗi: (.+)$/,m=>`error: ${tr(m[1])}`],
 [/^lỗi, mã (-?\d+)$/,m=>`error, code ${m[1]}`],
 [/^(\d+) token\/người\/ngày, (\S+) USD\/ngày$/,m=>`${m[1]} tokens/user/day, ${m[2]} USD/day`],
 [/^câu #(\d+)$/,m=>`question #${m[1]}`],
 [/^Bảng điều khiển bật \(build (.+)\)$/,m=>`Dashboard started (build ${m[1]})`],
 [/^tên: (.+)$/,m=>`name: ${m[1]}`,1],
 [/^cổng (\d+)$/,m=>`port ${m[1]}`,1],
 [/^đã tắt (.+)$/,m=>`stopped ${m[1]}`,1],
 [/^(\d+) ký tự$/,m=>`${m[1]} characters`,1],
 [/^(\d+) token$/,m=>`${m[1]} tokens`,1],
 [/^(\d+)\+(\d+) token$/,m=>`${m[1]}+${m[2]} tokens`,1],
 [/^(\d+) vào \+ (\d+) ra token$/,m=>`${m[1]} in + ${m[2]} out tokens`,1],
 [/^nguồn: (.+)$/,m=>`sources: ${m[1]}`,1],
 [/^(.+) \((\d+)s\)$/,m=>{const x=one(m[1]);return x===undefined?undefined:`${x} (${m[2]}s)`}],
];
function one(k,wholeOnly){
  const o=EN[k];if(o!==undefined)return o;
  for(const [re,fn,seg] of rules){if(wholeOnly&&seg)continue;const m=k.match(re);if(m){const x=fn(m);if(x!==undefined)return x}}
}
// Dịch một chuỗi tiếng Việt sang ngôn ngữ đang chọn. Chuỗi ghép bằng " · " được dịch từng đoạn.
function tr(s){
  if(lang!=='en'||typeof s!=='string')return s;
  const k=norm(s);if(!k)return s;
  const comp=k.includes(' · ');
  let o=one(k,comp);                       // chuỗi ghép: thử khớp nguyên chuỗi (không dùng luật dành cho đoạn) trước
  if(o===undefined&&comp){let ch=false;const parts=k.split(' · ').map(p=>{const x=one(p);if(x!==undefined){ch=true;return x}return p});if(ch)o=parts.join(' · ')}
  return o===undefined?s:s.replace(s.trim(),()=>o);
}
const SKIP={SCRIPT:1,STYLE:1,TEXTAREA:1},ATTRS=['placeholder','title'];
function fixText(n){
  const p=n.parentNode;if(!p||SKIP[p.nodeName])return;
  if(n.__out===undefined||n.nodeValue!==n.__out)n.__vi=n.nodeValue;     // văn bản mới do ứng dụng ghi: coi là bản gốc
  const out=tr(n.__vi);n.__out=out;if(n.nodeValue!==out)n.nodeValue=out;
}
function fixAttrs(el){
  for(const a of ATTRS){
    if(!el.hasAttribute||!el.hasAttribute(a))continue;
    const key='__a_'+a,cur=el.getAttribute(a);
    if(!el[key]||cur!==el[key].out)el[key]={vi:cur,out:cur};
    const out=tr(el[key].vi);el[key].out=out;if(cur!==out)el.setAttribute(a,out);
  }
}
function walk(root){
  if(!root)return;
  if(root.nodeType===3){fixText(root);return}
  if(root.nodeType!==1)return;
  fixAttrs(root);      // thuộc tính (placeholder, title) của mọi phần tử, kể cả textarea; chữ bên trong script/style/textarea bị fixText bỏ qua
  const tw=document.createTreeWalker(root,5);
  let n;while((n=tw.nextNode())){n.nodeType===3?fixText(n):fixAttrs(n)}
}
function toggles(){document.querySelectorAll('[data-lang]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.lang===lang)))}
function set(l){
  lang=l==='en'?'en':'vi';try{localStorage.setItem('lang',lang)}catch(e){}
  document.documentElement.lang=lang;walk(document.documentElement);toggles();
  try{document.dispatchEvent(new CustomEvent('langchange'))}catch(e){}
}
function init(){
  if(typeof document==='undefined'||!document.documentElement||typeof MutationObserver==='undefined')return;
  document.documentElement.lang=lang;toggles();if(lang==='en')walk(document.documentElement);
  new MutationObserver(recs=>{for(const r of recs){
    if(r.type==='childList')r.addedNodes.forEach(walk);
    else if(r.type==='characterData')fixText(r.target);
    else if(r.type==='attributes')fixAttrs(r.target);
  }}).observe(document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:ATTRS});
  document.addEventListener('click',e=>{const b=e.target&&e.target.closest&&e.target.closest('[data-lang]');if(b)set(b.dataset.lang)});
}
try{init()}catch(e){}
return {tr,set,get lang(){return lang},get loc(){return lang==='en'?'en-US':'vi-VN'},confirm:m=>confirm(tr(m))};
})();