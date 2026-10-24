(function () {
  var FA = "۰۱۲۳۴۵۶۷۸۹";
  function fa(n) { return n.toLocaleString("en-US").replace(/\d/g, function (d) { return FA[d]; }).replace(/,/g, "٬"); }
  function toEn(s) { return String(s).replace(/[۰-۹]/g, function (d) { return FA.indexOf(d); }); }

  // copy buttons
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var t = b.getAttribute("data-copy"), label = b.textContent;
      var done = function () { b.textContent = "کپی شد ✓"; setTimeout(function () { b.textContent = label; }, 1800); };
      if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(t).then(done); }
      else { var a = document.createElement("textarea"); a.value = t; document.body.appendChild(a); a.select(); document.execCommand("copy"); a.remove(); done(); }
    });
  });

  // confirm + double-submit guard
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!confirm(f.getAttribute("data-confirm"))) e.preventDefault(); });
  });
  document.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      setTimeout(function () { f.querySelectorAll("button:not([type=button])").forEach(function (b) { b.disabled = true; }); }, 0);
    });
  });

  // buy form: chips, stepper, live total
  var form = document.getElementById("buy");
  if (!form) return;
  var price = parseInt(form.getAttribute("data-price"), 10);
  var input = document.getElementById("gb"), total = document.getElementById("total");
  var min = parseInt(input.min, 10) || 1, max = parseInt(input.max, 10) || 1000;
  var chips = form.querySelectorAll(".chip");

  function update() {
    var n = parseInt(toEn(input.value), 10);
    if (isNaN(n)) n = 0;
    total.textContent = n >= min && n <= max ? fa(n * price) : "—";
    chips.forEach(function (c) { c.classList.toggle("on", parseInt(c.getAttribute("data-gb"), 10) === n); });
  }
  function set(n) { input.value = Math.max(min, Math.min(max, n)); update(); }

  chips.forEach(function (c) { c.addEventListener("click", function () { set(parseInt(c.getAttribute("data-gb"), 10)); }); });
  form.querySelectorAll(".step").forEach(function (s) {
    s.addEventListener("click", function () { set((parseInt(toEn(input.value), 10) || 0) + parseInt(s.getAttribute("data-step"), 10)); });
  });
  input.addEventListener("input", update);
  update();
})();
