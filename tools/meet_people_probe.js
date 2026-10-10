// วางโค้ดนี้ใน DevTools > Console ของแท็บ Google Meet (เปิดแผง "บุคคล" ที่แสดงรายชื่อไว้ก่อน) แล้วกด Enter
// มันอ่านอย่างเดียว ไม่แก้อะไรในหน้า แล้วพิมพ์โครงสร้างของแผงรายชื่อออกมาเป็น JSON ก้อนเดียว
// ให้คัดลอกผลทั้งก้อนส่งกลับมา (ชื่อคนในนั้นจะแก้เป็น "X" ก่อนก็ได้ ผมต้องการแค่โครงสร้าง/ชื่อ attribute)
// เป้าหมาย: ให้ MeetSync อ่านรายชื่อคนในห้องได้ตรงกับหน้า Meet จริง (selector ตอนนี้เขียนตามที่คาดไว้)
(() => {
  const cut = (s, n) => String(s || '').split(' ').filter((x) => x).join(' ').slice(0, n);
  const brief = (e) => ({
    tag: e.tagName.toLowerCase(),
    role: e.getAttribute('role'),
    aria: e.getAttribute('aria-label'),
    jsname: e.getAttribute('jsname'),
    cls: cut(typeof e.className === 'string' ? e.className : '', 70),
    kids: e.children.length,
    text: cut(e.textContent, 70),
  });
  const out = { url: location.pathname, lists: [], buttons: [], headings: [], firstRows: [] };

  for (const l of document.querySelectorAll('[role="list"]')) {
    out.lists.push({ ...brief(l), items: l.querySelectorAll('[role="listitem"]').length });
  }
  for (const b of document.querySelectorAll('button[aria-label]')) {
    if (out.buttons.length < 30) out.buttons.push({ aria: b.getAttribute('aria-label'), pressed: b.getAttribute('aria-pressed') });
  }
  // หัวข้อของแผง/กลุ่มรายชื่อ (ข้อความตรงๆ ในภาษาไทยหรืออังกฤษ) แล้วไล่ขึ้นไปดูตัวห่อ 4 ชั้น
  const wanted = ['บุคคล', 'ผู้มีส่วนร่วม', 'ในการประชุม', 'People', 'Contributors', 'In the meeting'];
  for (const e of document.querySelectorAll('div, span, h1, h2, h3')) {
    if (e.children.length === 0 && wanted.includes(cut(e.textContent, 40))) {
      const chain = [];
      let p = e;
      for (let i = 0; i < 5 && p; i += 1, p = p.parentElement) chain.push(brief(p));
      out.headings.push({ text: cut(e.textContent, 40), chain });
    }
  }
  // แถวคนแรก ๆ ในแผง: HTML ตัดสั้น ๆ
  const rows = document.querySelectorAll('[role="list"] [role="listitem"]');
  for (let i = 0; i < rows.length && out.firstRows.length < 3; i += 1) {
    out.firstRows.push(cut(rows[i].outerHTML, 900));
  }
  console.log(JSON.stringify(out, null, 1));
  return 'พิมพ์ผลแล้ว — คัดลอกก้อน JSON ด้านบนทั้งหมด';
})();
