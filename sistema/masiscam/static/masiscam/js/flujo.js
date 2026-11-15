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
    let total=0n, complete=true;
    form.querySelectorAll('[data-cantidad]').forEach(row => {
      const price=scaled(row.querySelector('[data-precio]').value,2);
      const qty=scaled(row.dataset.cantidad,6);
      const output=row.querySelector('[data-total-pieza]');
      if (price===null || qty===null) {output.textContent='—';complete=false;return;}
      const value=price*qty;
      let cents=value/1000000n;
      const remainder=value%1000000n;
      if (remainder>500000n || remainder===500000n && cents%2n) cents++;
      output.textContent=money(cents);total+=cents;
    });
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
