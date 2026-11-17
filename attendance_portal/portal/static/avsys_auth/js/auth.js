(function () {
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var root = document.documentElement;
  var card = document.getElementById('card');

  /* ---- 3D tilt: card leans toward the mouse, the photo layers shift by depth ---- */
  if (card && finePointer && !reduce) {
    var tx = 0, ty = 0, cx = 0, cy = 0, gx = 50, gy = 30, tgx = 50, tgy = 30;
    var tbx = 0, tby = 0, cbx = 0, cby = 0, running = false;

    function frame() {
      cx += (tx - cx) * 0.09;   cy += (ty - cy) * 0.09;
      gx += (tgx - gx) * 0.12;  gy += (tgy - gy) * 0.12;
      cbx += (tbx - cbx) * 0.06; cby += (tby - cby) * 0.06;
      card.style.setProperty('--ry', cx.toFixed(2) + 'deg');
      card.style.setProperty('--rx', cy.toFixed(2) + 'deg');
      card.style.setProperty('--gx', gx.toFixed(1) + '%');
      card.style.setProperty('--gy', gy.toFixed(1) + '%');
      root.style.setProperty('--bgy', cby.toFixed(2) + 'deg');   /* photo turns left / right  */
      root.style.setProperty('--bgx', cbx.toFixed(2) + 'deg');   /* photo turns up / down     */
      var moving = Math.abs(tx - cx) > 0.02 || Math.abs(ty - cy) > 0.02 ||
                   Math.abs(tgx - gx) > 0.2 || Math.abs(tgy - gy) > 0.2 ||
                   Math.abs(tbx - cbx) > 0.01 || Math.abs(tby - cby) > 0.01;
      if (moving) { requestAnimationFrame(frame); } else { running = false; }
    }
    function kick() { if (!running) { running = true; requestAnimationFrame(frame); } }

    window.addEventListener('mousemove', function (e) {
      var nx = e.clientX / window.innerWidth - 0.5;
      var ny = e.clientY / window.innerHeight - 0.5;
      var r = card.getBoundingClientRect();
      var over = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      var k = over ? 0.3 : 1;          /* card holds steadier while the pointer is on the form */
      tx = nx * 12 * k;                /* card: max ~6deg left/right */
      ty = -ny * 9 * k;                /* card: max ~4.5deg up/down  */
      tby = nx * 7;                    /* photo: max ~3.5deg left/right */
      tbx = -ny * 5;                   /* photo: max ~2.5deg up/down    */
      tgx = Math.max(0, Math.min(100, (e.clientX - r.left) / r.width * 100));
      tgy = Math.max(0, Math.min(100, (e.clientY - r.top) / r.height * 100));
      kick();
    }, { passive: true });
    document.addEventListener('mouseleave', function () { tx = ty = tbx = tby = 0; tgx = 50; tgy = 30; kick(); });
  }

  /* ---- show / hide password buttons ---- */
  var eye = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var eyeOff = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 5.7A10.6 10.6 0 0 1 12 5.5c6.4 0 10 6.5 10 6.5a17 17 0 0 1-3.2 4M6.5 6.9C3.9 8.6 2 12 2 12s3.6 6.5 10 6.5c1.5 0 2.8-.3 4-.8M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    var wrap = document.createElement('div');
    wrap.className = 'pw';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-toggle';
    btn.setAttribute('aria-label', 'Show password');
    btn.innerHTML = eye;
    btn.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.innerHTML = show ? eyeOff : eye;
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    });
    wrap.appendChild(btn);
  });

  /* ---- button shows progress while the form is sent ---- */
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var b = form.querySelector('button.btn[type="submit"]');
      if (!b) return;
      b.classList.add('loading');
      setTimeout(function () { b.disabled = true; }, 0);
    });
  });
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('button.btn').forEach(function (b) { b.classList.remove('loading'); b.disabled = false; });
  });
})();
