document.querySelectorAll("[data-copy]").forEach(function (b) {
  b.addEventListener("click", function () {
    var t = b.getAttribute("data-copy"), label = b.textContent;
    var done = function () { b.textContent = "کپی شد ✓"; setTimeout(function () { b.textContent = label; }, 1800); };
    if (navigator.clipboard) { navigator.clipboard.writeText(t).then(done); }
    else { var a = document.createElement("textarea"); a.value = t; document.body.appendChild(a); a.select(); document.execCommand("copy"); a.remove(); done(); }
  });
});
document.querySelectorAll("form[data-confirm]").forEach(function (f) {
  f.addEventListener("submit", function (e) { if (!confirm(f.getAttribute("data-confirm"))) e.preventDefault(); });
});
document.querySelectorAll("form").forEach(function (f) {
  f.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    setTimeout(function () { f.querySelectorAll("button[type=submit],button:not([type])").forEach(function (b) { b.disabled = true; }); }, 0);
  });
});
