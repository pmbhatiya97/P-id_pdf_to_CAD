"use strict";
(() => {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const panel = document.getElementById("demo-panel");
  const format = document.getElementById("output-format");
  const mode = document.getElementById("drawing-mode");
  const filetype = document.getElementById("file-type");
  const filename = document.getElementById("demo-filename");
  const status = document.getElementById("demo-status");
  const fileStatus = document.getElementById("file-status");
  const convert = document.getElementById("convert-demo");
  const app = document.querySelector(".app-window");
  let direction = "pdf";
  let timer = null;
  function reset() {
    if (timer) window.clearTimeout(timer);
    timer = null;
    app.classList.remove("is-converting");
    convert.disabled = false;
    status.textContent = "Ready when your drawing is.";
    fileStatus.textContent = "Ready";
  }
  function choose(index) {
    reset();
    direction = index === 0 ? "pdf" : "dwg";
    tabs.forEach((tab, i) => {
      tab.setAttribute("aria-selected", String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby", tabs[index].id);
    filetype.textContent = direction.toUpperCase();
    filename.textContent = `courtyard_studio.${direction}`;
    format.replaceChildren(...(direction === "pdf" ? ["DWG · R2018", "DXF · R2018"] : ["PDF · A3 landscape", "PDF · A1 landscape"]).map(text => new Option(text, text)));
    mode.replaceChildren(...(direction === "pdf" ? ["2D Vector", "2D Raster / Scanned", "P&ID (AI)"] : ["Fit to page", "Scale 1:100"]).map(text => new Option(text, text)));
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => choose(index));
    tab.addEventListener("keydown", event => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        choose(next);
        tabs[next].focus();
      }
    });
  });
  format.addEventListener("change", reset);
  mode.addEventListener("change", reset);
  convert.addEventListener("click", () => {
    reset();
    if (mode.value === "P&ID (AI)") {
      status.textContent = "P&ID workflow: configure a VLM, then review extraction before CAD export.";
      fileStatus.textContent = "Review required";
      return;
    }
    convert.disabled = true;
    app.classList.add("is-converting");
    status.textContent = "Converting 18,420 entities… (illustrative demo)";
    fileStatus.textContent = "Converting";
    timer = window.setTimeout(() => {
      app.classList.remove("is-converting");
      convert.disabled = false;
      fileStatus.textContent = "Demo complete";
      status.textContent = "Two-way conversion ready · demo only, no file was processed.";
      timer = null;
    }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 50 : 1500);
  });
  document.getElementById("year").textContent = String(new Date().getFullYear());
})();
