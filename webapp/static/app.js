(function () {
  // ================================================================
  // Mode selector
  // ================================================================
  var modeBtns = document.querySelectorAll(".mode-btn");
  var modePanels = document.querySelectorAll(".mode-panel");

  modeBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var mode = btn.dataset.mode;
      modeBtns.forEach(function (b) { b.classList.toggle("active", b.dataset.mode === mode); });
      modePanels.forEach(function (p) { p.classList.toggle("active", p.id === "mode-" + mode); });
    });
  });

  // ================================================================
  // Direction A: C → YAML
  // ================================================================
  var cCode = document.getElementById("c-code");
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll(".tab-panel");
  var uploadZoneC = document.getElementById("upload-zone-c");
  var fileInputC = document.getElementById("file-input-c");
  var generateYamlBtn = document.getElementById("generate-yaml-btn");

  var yamlPipelineSection = document.getElementById("yaml-pipeline-section");
  var yamlPipelineProgress = document.getElementById("yaml-pipeline-progress");
  var yamlPipelineActions = document.getElementById("yaml-pipeline-actions");
  var downloadYamlAllBtn = document.getElementById("download-yaml-all-btn");
  var generateCFromRunBtn = document.getElementById("generate-c-from-run-btn");

  var yamlPreviewSection = document.getElementById("yaml-preview-section");
  var yamlPreviewTitle = document.getElementById("yaml-preview-title");
  var yamlPreviewTabs = document.getElementById("yaml-preview-tabs");
  var yamlPreview = document.getElementById("yaml-preview");

  var yamlRunId = null;
  var yamlFileResults = {};
  var yamlFileStreaming = {};
  var yamlActivePreview = null;

  // Input tabs
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.dataset.tab;
      tabs.forEach(function (t) { t.classList.remove("active"); });
      panels.forEach(function (p) { p.classList.toggle("active", p.id === "panel-" + name); });
      tab.classList.add("active");
    });
  });

  // C file upload
  uploadZoneC.addEventListener("click", function () { fileInputC.click(); });
  uploadZoneC.addEventListener("dragover", function (e) { e.preventDefault(); uploadZoneC.classList.add("dragover"); });
  uploadZoneC.addEventListener("dragleave", function () { uploadZoneC.classList.remove("dragover"); });
  uploadZoneC.addEventListener("drop", function (e) {
    e.preventDefault();
    uploadZoneC.classList.remove("dragover");
    var f = e.dataTransfer.files[0];
    if (f && (f.name.endsWith(".c") || f.name.endsWith(".h"))) loadCFile(f);
  });
  fileInputC.addEventListener("change", function () { if (fileInputC.files[0]) loadCFile(fileInputC.files[0]); });

  function loadCFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      cCode.value = reader.result;
      document.querySelector('[data-tab="paste"]').click();
    };
    reader.readAsText(file);
  }

  function resetYamlPipeline() {
    yamlPipelineProgress.innerHTML = "";
    yamlPipelineActions.classList.add("hidden");
    yamlPreviewSection.classList.add("hidden");
    yamlPreviewTabs.innerHTML = "";
    yamlPreview.textContent = "";
    yamlFileResults = {};
    yamlFileStreaming = {};
    yamlActivePreview = null;
    yamlRunId = null;
  }

  function createYamlPipelineItem(index, filename, label) {
    var el = document.createElement("div");
    el.className = "pipeline-item";
    el.id = "yaml-pipe-" + index;
    el.innerHTML =
      '<div class="pipeline-icon" id="yaml-pipe-icon-' + index + '"></div>' +
      '<span class="pipeline-label">' + label + '</span>' +
      '<span class="pipeline-filename">' + filename + '</span>';
    el.addEventListener("click", function () { showYamlPreview(filename); });
    yamlPipelineProgress.appendChild(el);
  }

  function setYamlPipelineState(index, state) {
    var el = document.getElementById("yaml-pipe-" + index);
    var icon = document.getElementById("yaml-pipe-icon-" + index);
    if (!el || !icon) return;
    el.className = "pipeline-item " + state;
    if (state === "active") icon.innerHTML = '<div class="spinner"></div>';
    else if (state === "done") { icon.textContent = "\u2713"; icon.style.color = "var(--success)"; }
    else if (state === "error") { icon.textContent = "\u2717"; icon.style.color = "var(--error)"; }
  }

  function showYamlPreview(filename) {
    yamlActivePreview = filename;
    yamlPreview.textContent = yamlFileResults[filename] || yamlFileStreaming[filename] || "(no content yet)";
    yamlPreviewTitle.textContent = "Preview \u2014 " + filename;
    yamlPreviewSection.classList.remove("hidden");
    yamlPreviewTabs.querySelectorAll(".preview-tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.file === filename);
    });
    yamlPreview.scrollTop = yamlPreview.scrollHeight;
  }

  function addYamlPreviewTab(filename) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preview-tab";
    btn.dataset.file = filename;
    btn.textContent = filename;
    btn.addEventListener("click", function () { showYamlPreview(filename); });
    yamlPreviewTabs.appendChild(btn);
  }

  function getYamlFilenameByIndex(index) {
    var el = document.getElementById("yaml-pipe-" + index);
    if (!el) return null;
    var span = el.querySelector(".pipeline-filename");
    return span ? span.textContent : null;
  }

  // Generate YAML
  generateYamlBtn.addEventListener("click", function () {
    var code = cCode.value.trim();
    if (!code) return;
    resetYamlPipeline();
    resetCPipeline();
    yamlPipelineSection.classList.remove("hidden");
    generateYamlBtn.disabled = true;

    streamNDJSON("/api/generate-stream", { c_code: code }, function (ev) {
      switch (ev.event) {
        case "file_start":
          createYamlPipelineItem(ev.index, ev.filename, ev.label);
          setYamlPipelineState(ev.index, "active");
          yamlFileStreaming[ev.filename] = "";
          addYamlPreviewTab(ev.filename);
          showYamlPreview(ev.filename);
          break;
        case "chunk":
          var fn = getYamlFilenameByIndex(ev.index);
          if (fn) {
            yamlFileStreaming[fn] = (yamlFileStreaming[fn] || "") + ev.text;
            if (yamlActivePreview === fn) {
              yamlPreview.textContent = yamlFileStreaming[fn];
              yamlPreview.scrollTop = yamlPreview.scrollHeight;
            }
          }
          break;
        case "file_done":
          setYamlPipelineState(ev.index, "done");
          yamlFileResults[ev.filename] = ev.yaml;
          if (yamlActivePreview === ev.filename) yamlPreview.textContent = ev.yaml;
          break;
        case "file_error":
          setYamlPipelineState(ev.index, "error");
          break;
        case "pipeline_done":
          yamlRunId = ev.run_id;
          generateYamlBtn.disabled = false;
          yamlPipelineActions.classList.remove("hidden");
          break;
      }
    }, function () { generateYamlBtn.disabled = false; });
  });

  downloadYamlAllBtn.addEventListener("click", function () {
    if (yamlRunId) window.location.href = "/api/download-all/" + yamlRunId;
  });

  // Generate C from current run
  generateCFromRunBtn.addEventListener("click", function () {
    if (!yamlRunId) return;
    generateCFromRunBtn.disabled = true;
    resetCPipeline();
    cPipelineSection.classList.remove("hidden");

    streamNDJSON("/api/generate-c-stream", { run_id: yamlRunId }, handleCEvent, function () {
      generateCFromRunBtn.disabled = false;
    });
  });

  // ================================================================
  // Direction B: YAML → C (upload)
  // ================================================================
  var uploadZoneYaml = document.getElementById("upload-zone-yaml");
  var fileInputYaml = document.getElementById("file-input-yaml");
  var yamlUploadStatus = document.getElementById("yaml-upload-status");
  var generateCUploadBtn = document.getElementById("generate-c-upload-btn");
  var uploadedYamlFile = null;

  uploadZoneYaml.addEventListener("click", function () { fileInputYaml.click(); });
  uploadZoneYaml.addEventListener("dragover", function (e) { e.preventDefault(); uploadZoneYaml.classList.add("dragover"); });
  uploadZoneYaml.addEventListener("dragleave", function () { uploadZoneYaml.classList.remove("dragover"); });
  uploadZoneYaml.addEventListener("drop", function (e) {
    e.preventDefault();
    uploadZoneYaml.classList.remove("dragover");
    var f = e.dataTransfer.files[0];
    if (f) handleYamlUpload(f);
  });
  fileInputYaml.addEventListener("change", function () {
    if (fileInputYaml.files[0]) handleYamlUpload(fileInputYaml.files[0]);
  });

  function handleYamlUpload(file) {
    var ok = file.name.endsWith(".zip") || file.name.endsWith(".yaml") || file.name.endsWith(".yml");
    if (!ok) {
      yamlUploadStatus.textContent = "Please upload a .zip of YAML files or a single .yaml file";
      yamlUploadStatus.className = "upload-status err";
      generateCUploadBtn.disabled = true;
      uploadedYamlFile = null;
      return;
    }
    uploadedYamlFile = file;
    yamlUploadStatus.textContent = "Ready: " + file.name + " (" + Math.round(file.size / 1024) + " KB)";
    yamlUploadStatus.className = "upload-status ok";
    generateCUploadBtn.disabled = false;
  }

  generateCUploadBtn.addEventListener("click", function () {
    if (!uploadedYamlFile) return;
    generateCUploadBtn.disabled = true;
    resetCPipeline();
    cPipelineSection.classList.remove("hidden");

    var formData = new FormData();
    formData.append("file", uploadedYamlFile);

    streamNDJSONUpload("/api/upload-yaml-generate-c-stream", formData, handleCEvent, function () {
      generateCUploadBtn.disabled = false;
    });
  });

  // ================================================================
  // C Code Generation Pipeline (shared)
  // ================================================================
  var cPipelineSection = document.getElementById("c-pipeline-section");
  var cPipelineProgress = document.getElementById("c-pipeline-progress");
  var cPipelineActions = document.getElementById("c-pipeline-actions");
  var downloadCAllBtn = document.getElementById("download-c-all-btn");

  var cPreviewSection = document.getElementById("c-preview-section");
  var cPreviewTitle = document.getElementById("c-preview-title");
  var cPreviewTabs = document.getElementById("c-preview-tabs");
  var cPreview = document.getElementById("c-preview");

  var cRunId = null;
  var cFileResults = {};
  var cFileStreaming = {};
  var cActivePreview = null;

  function resetCPipeline() {
    cPipelineProgress.innerHTML = "";
    cPipelineActions.classList.add("hidden");
    cPreviewSection.classList.add("hidden");
    cPreviewTabs.innerHTML = "";
    cPreview.textContent = "";
    cFileResults = {};
    cFileStreaming = {};
    cActivePreview = null;
    cRunId = null;
  }

  function createCPipelineItem(index, filename, label) {
    var el = document.createElement("div");
    el.className = "pipeline-item";
    el.id = "c-pipe-" + index;
    el.innerHTML =
      '<div class="pipeline-icon" id="c-pipe-icon-' + index + '"></div>' +
      '<span class="pipeline-label">' + label + '</span>' +
      '<span class="pipeline-filename">' + filename + '</span>';
    el.addEventListener("click", function () { showCPreview(filename); });
    cPipelineProgress.appendChild(el);
  }

  function setCPipelineState(index, state) {
    var el = document.getElementById("c-pipe-" + index);
    var icon = document.getElementById("c-pipe-icon-" + index);
    if (!el || !icon) return;
    el.className = "pipeline-item " + state;
    if (state === "active") icon.innerHTML = '<div class="spinner"></div>';
    else if (state === "done") { icon.textContent = "\u2713"; icon.style.color = "var(--success)"; }
    else if (state === "error") { icon.textContent = "\u2717"; icon.style.color = "var(--error)"; }
  }

  function showCPreview(filename) {
    cActivePreview = filename;
    cPreview.textContent = cFileResults[filename] || cFileStreaming[filename] || "(no content yet)";
    cPreviewTitle.textContent = "Preview \u2014 " + filename;
    cPreviewSection.classList.remove("hidden");
    cPreviewTabs.querySelectorAll(".preview-tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.file === filename);
    });
    cPreview.scrollTop = cPreview.scrollHeight;
  }

  function addCPreviewTab(filename) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preview-tab";
    btn.dataset.file = filename;
    btn.textContent = filename;
    btn.addEventListener("click", function () { showCPreview(filename); });
    cPreviewTabs.appendChild(btn);
  }

  function getCFilenameByIndex(index) {
    var el = document.getElementById("c-pipe-" + index);
    if (!el) return null;
    var span = el.querySelector(".pipeline-filename");
    return span ? span.textContent : null;
  }

  function handleCEvent(ev) {
    switch (ev.event) {
      case "c_file_start":
        createCPipelineItem(ev.index, ev.filename, ev.label);
        setCPipelineState(ev.index, "active");
        cFileStreaming[ev.filename] = "";
        addCPreviewTab(ev.filename);
        showCPreview(ev.filename);
        break;
      case "c_chunk":
        var fn = getCFilenameByIndex(ev.index);
        if (fn) {
          cFileStreaming[fn] = (cFileStreaming[fn] || "") + ev.text;
          if (cActivePreview === fn) {
            cPreview.textContent = cFileStreaming[fn];
            cPreview.scrollTop = cPreview.scrollHeight;
          }
        }
        break;
      case "c_file_done":
        setCPipelineState(ev.index, "done");
        cFileResults[ev.filename] = ev.code;
        if (cActivePreview === ev.filename) cPreview.textContent = ev.code;
        break;
      case "c_file_error":
        setCPipelineState(ev.index, "error");
        break;
      case "c_pipeline_done":
        cRunId = ev.c_run_id;
        cPipelineActions.classList.remove("hidden");
        break;
    }
  }

  downloadCAllBtn.addEventListener("click", function () {
    if (cRunId) window.location.href = "/api/download-c-all/" + cRunId;
  });

  // ================================================================
  // Shared streaming helpers
  // ================================================================
  function streamNDJSON(url, body, onEvent, onDone) {
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (res) {
        if (!res.ok) return res.text().then(function (t) { throw new Error(t || res.statusText); });
        return pumpStream(res, onEvent);
      })
      .then(onDone)
      .catch(function (err) {
        alert("Error: " + (err.message || "Unknown"));
        if (onDone) onDone();
      });
  }

  function streamNDJSONUpload(url, formData, onEvent, onDone) {
    fetch(url, { method: "POST", body: formData })
      .then(function (res) {
        if (!res.ok) return res.text().then(function (t) { throw new Error(t || res.statusText); });
        return pumpStream(res, onEvent);
      })
      .then(onDone)
      .catch(function (err) {
        alert("Error: " + (err.message || "Unknown"));
        if (onDone) onDone();
      });
  }

  function pumpStream(res, onEvent) {
    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) return;
        buffer += decoder.decode(result.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line) continue;
          try { onEvent(JSON.parse(line)); } catch (e) {}
        }
        return pump();
      });
    }
    return pump();
  }
})();
