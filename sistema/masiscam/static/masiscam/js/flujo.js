(() => {
  const form = document.querySelector('#oficina-cotizacion');
  if (!form) return;
  const money = cents => `${cents / 100n}.${String(cents % 100n).padStart(2,'0')}`;
  const scaled = (text, digits) => {
    if (!new RegExp(`^\\d+(?:\\.\\d{1,${digits}})?$`).test(text)) return null;
    const [whole, fraction=''] = text.split('.');
    return BigInt(whole + fraction.padEnd(digits,'0'));
  };
  function totals() {
    let total=0n, algunaLinea=false, complete=true;
    form.querySelectorAll('[data-linea]').forEach(row => {
      const descInput=row.querySelector('[name$="_descripcion"]');
      const desc=descInput ? descInput.value.trim() : 'concepto';
      const priceText=row.querySelector('[name$="_precio"]').value.trim();
      const qtyInput=row.querySelector('[name$="_cantidad"]');
      const qtyText=(qtyInput ? qtyInput.value.trim() : row.dataset.cantidad) || '1';
      const output=row.querySelector('[data-total-linea]');
      if (!desc && !priceText) {output.textContent='';return;}
      if (row.dataset.concepto && !priceText) {output.textContent='—';complete=false;return;}
      const price=scaled(priceText,2), qty=scaled(qtyText,6);
      if (!desc || price===null || qty===null) {output.textContent='—';complete=false;return;}
      const value=price*qty;
      let cents=value/1000000n;
      const remainder=value%1000000n;
      if (remainder>500000n || remainder===500000n && cents%2n) cents++;
      output.textContent=money(cents);total+=cents;algunaLinea=true;
    });
    complete=complete&&algunaLinea;
    document.querySelector('#oficina-total').textContent=complete?money(total):'—';
    form.querySelectorAll('[data-total-general]').forEach(output=>output.textContent=complete?money(total):'—');
  }
  form.addEventListener('input',totals);totals();
  for (const sheet of [form,document.querySelector('#oficina-enviar')].filter(Boolean)) {
    sheet.addEventListener('submit',event=>{
      if (event.defaultPrevented) return;
      if (sheet.dataset.sending) {event.preventDefault();return;}
      sheet.dataset.sending='true';
      sheet.querySelectorAll('button[type=submit],button:not([type])').forEach(button=>{button.disabled=true;});
    });
  }
})();
