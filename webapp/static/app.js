(function () {
  var cCode = document.getElementById("c-code");
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll(".tab-panel");
  var uploadZone = document.getElementById("upload-zone");
  var fileInput = document.getElementById("file-input");
  var generateBtn = document.getElementById("generate-btn");

  var pipelineSection = document.getElementById("pipeline-section");
  var pipelineProgress = document.getElementById("pipeline-progress");
  var pipelineActions = document.getElementById("pipeline-actions");
  var downloadAllBtn = document.getElementById("download-all-btn");

  var previewSection = document.getElementById("preview-section");
  var previewTitle = document.getElementById("preview-title");
  var previewTabs = document.getElementById("preview-tabs");
  var yamlPreview = document.getElementById("yaml-preview");

  var runId = null;
  var fileResults = {};      // filename -> yaml content
  var fileStreaming = {};     // filename -> partial content so far
  var activePreview = null;

  // --- Input tabs ---
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.dataset.tab;
      tabs.forEach(function (t) { t.classList.remove("active"); });
      panels.forEach(function (p) { p.classList.toggle("active", p.id === "panel-" + name); });
      tab.classList.add("active");
    });
  });

  // --- Upload ---
  uploadZone.addEventListener("click", function () { fileInput.click(); });

  uploadZone.addEventListener("dragover", function (e) {
    e.preventDefault();
    uploadZone.classList.add("dragover");
  });
  uploadZone.addEventListener("dragleave", function () {
    uploadZone.classList.remove("dragover");
  });
  uploadZone.addEventListener("drop", function (e) {
    e.preventDefault();
    uploadZone.classList.remove("dragover");
    var file = e.dataTransfer.files[0];
    if (file && (file.name.endsWith(".c") || file.name.endsWith(".h"))) {
      loadFile(file);
    }
  });
  fileInput.addEventListener("change", function () {
    if (fileInput.files[0]) loadFile(fileInput.files[0]);
  });

  function loadFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      cCode.value = reader.result;
      document.querySelector('[data-tab="paste"]').click();
    };
    reader.readAsText(file);
  }

  // --- Pipeline UI helpers ---

  function resetPipeline() {
    pipelineProgress.innerHTML = "";
    pipelineActions.classList.add("hidden");
    previewSection.classList.add("hidden");
    previewTabs.innerHTML = "";
    yamlPreview.textContent = "";
    fileResults = {};
    fileStreaming = {};
    activePreview = null;
    runId = null;
  }

  function createPipelineItem(index, filename, label) {
    var el = document.createElement("div");
    el.className = "pipeline-item";
    el.id = "pipe-" + index;
    el.innerHTML =
      '<div class="pipeline-icon" id="pipe-icon-' + index + '"></div>' +
      '<span class="pipeline-label">' + label + '</span>' +
      '<span class="pipeline-filename">' + filename + '</span>';
    el.addEventListener("click", function () { showPreview(filename); });
    pipelineProgress.appendChild(el);
  }

  function setPipelineState(index, state) {
    var el = document.getElementById("pipe-" + index);
    var icon = document.getElementById("pipe-icon-" + index);
    if (!el || !icon) return;
    el.className = "pipeline-item " + state;
    if (state === "active") {
      icon.innerHTML = '<div class="spinner"></div>';
    } else if (state === "done") {
      icon.textContent = "\u2713";
      icon.style.color = "var(--success)";
    } else if (state === "error") {
      icon.textContent = "\u2717";
      icon.style.color = "var(--error)";
    }
  }

  function showPreview(filename) {
    activePreview = filename;
    var content = fileResults[filename] || fileStreaming[filename] || "(no content yet)";
    yamlPreview.textContent = content;
    previewTitle.textContent = "Preview — " + filename;
    previewSection.classList.remove("hidden");
    // highlight tab
    var ptabs = previewTabs.querySelectorAll(".preview-tab");
    ptabs.forEach(function (t) {
      t.classList.toggle("active", t.dataset.file === filename);
    });
    yamlPreview.scrollTop = yamlPreview.scrollHeight;
  }

  function addPreviewTab(filename) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preview-tab";
    btn.dataset.file = filename;
    btn.textContent = filename;
    btn.addEventListener("click", function () { showPreview(filename); });
    previewTabs.appendChild(btn);
  }

  // --- Main generate ---
  generateBtn.addEventListener("click", function () {
    var code = cCode.value.trim();
    if (!code) return;

    resetPipeline();
    pipelineSection.classList.remove("hidden");
    generateBtn.disabled = true;

    fetch("/api/generate-stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ c_code: code }),
    })
      .then(function (res) {
        if (!res.ok) return res.text().then(function (t) { throw new Error(t || res.statusText); });
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";

        function pump() {
          return reader.read().then(function (result) {
            if (result.done) {
              generateBtn.disabled = false;
              return;
            }
            buffer += decoder.decode(result.value, { stream: true });
            var lines = buffer.split("\n");
            buffer = lines.pop();
            for (var i = 0; i < lines.length; i++) {
              var line = lines[i].trim();
              if (!line) continue;
              try { processEvent(JSON.parse(line)); } catch (e) {}
            }
            return pump();
          });
        }
        return pump();
      })
      .catch(function (err) {
        generateBtn.disabled = false;
        alert("Error: " + (err.message || "Unknown"));
      });
  });

  function processEvent(ev) {
    switch (ev.event) {
      case "file_start":
        createPipelineItem(ev.index, ev.filename, ev.label);
        setPipelineState(ev.index, "active");
        fileStreaming[ev.filename] = "";
        addPreviewTab(ev.filename);
        showPreview(ev.filename);
        break;

      case "chunk":
        var fname = getFilenameByIndex(ev.index);
        if (fname) {
          fileStreaming[fname] = (fileStreaming[fname] || "") + ev.text;
          if (activePreview === fname) {
            yamlPreview.textContent = fileStreaming[fname];
            yamlPreview.scrollTop = yamlPreview.scrollHeight;
          }
        }
        break;

      case "file_done":
        setPipelineState(ev.index, "done");
        fileResults[ev.filename] = ev.yaml;
        if (activePreview === ev.filename) {
          yamlPreview.textContent = ev.yaml;
        }
        break;

      case "file_error":
        setPipelineState(ev.index, "error");
        var fn = getFilenameByIndex(ev.index);
        if (fn && activePreview === fn) {
          yamlPreview.textContent = "ERROR: " + (ev.error || "Unknown error");
        }
        break;

      case "pipeline_done":
        runId = ev.run_id;
        generateBtn.disabled = false;
        pipelineActions.classList.remove("hidden");
        break;
    }
  }

  function getFilenameByIndex(index) {
    var el = document.getElementById("pipe-" + index);
    if (!el) return null;
    var span = el.querySelector(".pipeline-filename");
    return span ? span.textContent : null;
  }

  // --- Download all ZIP ---
  downloadAllBtn.addEventListener("click", function () {
    if (!runId) return;
    window.location.href = "/api/download-all/" + runId;
  });
})();
