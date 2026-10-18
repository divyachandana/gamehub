/*
 * Copyright (c) Qualcomm Innovation Center, Inc.
 * All rights reserved.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree.
 */

/**
 * @file
 *
 * Persistent variant of qnn_llama_runner: loads the model and restores the
 * QNN context once, then serves prompts over a Windows named pipe instead of
 * exiting after one prompt. Intended as a base for a local client (e.g. an
 * OpenAI-API-shaped wrapper) to be built on top of, without paying the
 * model-load / QNN-context-restore cost on every request.
 *
 * Not Qwen-specific or llama-specific: --decoder_model_version selects the
 * same set of decoder models qnn_llama_runner supports (Llama2/3, Gemma/2/3/4,
 * Granite, Phi4, Qwen2.5/3, SmolLM2/3, GLM, Codegen) via the same
 * get_formatted_prompt() switch, copied from qnn_llama_runner.cpp unchanged.
 *
 * Protocol: newline-delimited JSON, one request/response per line, over
 * \\.\pipe\<--pipe_name>.
 *
 *   Request:  {"prompt": "...", "system_prompt": "", "seq_len": 128,
 *              "temperature": 0.0, "include_metrics": false}
 *   Response: {"ok": true, "text": "..."} or, with include_metrics: true,
 *             {"ok": true, "text": "...", "metrics": {...}} (same shape as
 *             stats_to_json_string(), i.e. the PyTorchObserver JSON already
 *             printed by qnn_llama_runner). On failure:
 *             {"ok": false, "error": "..."}
 *   {"cmd": "shutdown"} closes the connection and exits the process.
 *   {"cmd": "reset"} starts a fresh conversation (KV position back to 0) -> {"ok": true, "reset": true}.
 *   Request extras: "reset": true resets before this prompt (one-shot requests that never
 *   accumulate context); "stream": true sends {"delta": "..."} lines before the final response.
 *
 * IMPORTANT — two behaviors inherited from example::Runner, not introduced
 * here, that any client built on this host needs to design around:
 *
 * 1. Requests share ONE continuous conversation. Runner's KV-cache position
 *    (cur_pos_) advances across calls to generate_from_prompt_or_file and
 *    upstream Runner::reset() was a no-op stub; this tree's runner.h makes it
 *    set cur_pos_ = 0, so {"cmd": "reset"} starts a fresh conversation. seq_len in a
 *    request is the running total across the whole connection's history, not
 *    a per-request budget.
 * 2. Exceeding the compiled context length is a HARD PROCESS ABORT, not a
 *    recoverable error. Several checks inside Runner (e.g. "sequence length
 *    exceeded" in generate_from_prompt_or_file) use ET_CHECK_MSG, which logs
 *    Fatal and terminates the process — it does not return an Error this
 *    host could catch and turn into an {"ok": false, ...} response. A client
 *    should track its own token budget conservatively below the model's
 *    compiled max_context_len and restart this process for a new
 *    conversation, rather than relying on the host to reject an oversized
 *    request gracefully.
 */

#include <executorch/backends/qualcomm/runtime/QnnExecuTorch.h>
#include <executorch/examples/qualcomm/oss_scripts/llama/runner/runner.h>
#include <executorch/extension/llm/runner/irunner.h>
#include <executorch/extension/llm/runner/stats.h>
#include <executorch/runtime/backend/interface.h>
#include <executorch/runtime/platform/log.h>
#include <gflags/gflags.h>
#include <nlohmann/json.hpp>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

using json = nlohmann::json;

DEFINE_string(decoder_model_version, "llama2", "The decoder model to execute.");
DEFINE_string(
    model_path,
    "kv_llama_qnn.pte",
    "Model serialized in flatbuffer format.");
DEFINE_string(
    performance_output_path,
    "inference_speed.txt",
    "Records inference speed. For CI purpose.");
DEFINE_string(tokenizer_path, "tokenizer.bin", "Tokenizer stuff.");
DEFINE_double(
    temperature,
    0.0f,
    "Default temperature when a request doesn't specify one.");
DEFINE_int32(
    seq_len,
    128,
    "Default total sequence length (prompt + output) when a request doesn't specify one.");
DEFINE_int32(
    eval_mode,
    1,
    "0: TokenGenerator(kv) / 1: HybridMode (prefill+kv) / 2: Lookahead Decoding");
DEFINE_bool(
    shared_buffer,
    false,
    "Specifies to use shared buffers for zero-copy use case between the application and device/co-processor associated with the backend.");
DEFINE_int32(ngram, 0, "[Lookahead Decoding] n-gram size.");
DEFINE_int32(window, 0, "[Lookahead Decoding] window size.");
DEFINE_int32(gcap, 0, "[Lookahead Decoding] verification candidates.");
DEFINE_string(
    pipe_name,
    "qnn_llm_host",
    "Named pipe to serve on: \\\\.\\pipe\\<pipe_name>.");

namespace {

// Mirrors qnn_llama_runner.cpp's get_formatted_prompt, duplicated rather than
// shared since it's a small free function tied to that file's switch — see
// oss_scripts/llama/__init__.py's own TODO about eventually reading this from
// each model's shipped chat_template.jinja instead of a hardcoded switch.
std::string get_formatted_prompt(
    const std::string& prompt,
    const std::string& system_prompt,
    example::DecoderModelVersion decoder_model_version) {
  std::string formatted_prompt;
  switch (decoder_model_version) {
    case example::DecoderModelVersion::kLlama2:
    case example::DecoderModelVersion::kQwen2_5:
    case example::DecoderModelVersion::kCodegen:
      formatted_prompt.append(prompt);
      break;
    case example::DecoderModelVersion::kLlama3:
      if (!system_prompt.empty()) {
        formatted_prompt.append(
            "<|start_header_id|>system<|end_header_id|>\n\n");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<|eot_id|>");
      }
      formatted_prompt.append("<|start_header_id|>user<|end_header_id|>\n\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append(
          "<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n");
      break;
    case example::DecoderModelVersion::kGemma:
    case example::DecoderModelVersion::kGemma3:
      formatted_prompt.append("<start_of_turn>user\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<end_of_turn>\n");
      formatted_prompt.append("<start_of_turn>model\n");
      if (!system_prompt.empty()) {
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<end_of_turn>\n");
      }
      break;
    case example::DecoderModelVersion::kGemma2:
      formatted_prompt.append("<start_of_turn>user\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<end_of_turn>\n");
      formatted_prompt.append("<start_of_turn>model\n");
      break;
    case example::DecoderModelVersion::kGemma4:
      formatted_prompt.append("<bos>");
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|turn>system\n");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<turn|>\n");
      }
      formatted_prompt.append("<|turn>user\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<turn|>\n");
      formatted_prompt.append("<|turn>model\n");
      break;
    case example::DecoderModelVersion::kGranite:
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|start_of_role|>system<|end_of_role|>");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<|end_of_text|>\n");
      }
      formatted_prompt.append("<|start_of_role|>user<|end_of_role|>");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<|end_of_text|>\n");
      formatted_prompt.append("<|start_of_role|>assistant<|end_of_role|>");
      break;
    case example::DecoderModelVersion::kPhi4:
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|system|>");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<|end|>");
      }
      formatted_prompt.append("<|user|>");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<|end|><|assistant|>");
      break;
    case example::DecoderModelVersion::kQwen3:
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|im_start|>system\n");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<|im_end|>\n");
      }
      formatted_prompt.append("<|im_start|>user\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<|im_end|>\n");
      formatted_prompt.append("<|im_start|>assistant\n");
      break;
    case example::DecoderModelVersion::kSmollm2_135m:
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|im_start|>system\n");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("<|im_end|>\n");
      }
      formatted_prompt.append("<|im_start|>user\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<|im_end|>\n");
      formatted_prompt.append("<|im_start|>assistant\n\n");
      break;
    case example::DecoderModelVersion::kSmollm3:
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|im_start|>system\n");
        formatted_prompt.append(system_prompt);
        formatted_prompt.append("\n\n");
      }
      formatted_prompt.append("<|im_start|>user\n");
      formatted_prompt.append(prompt);
      formatted_prompt.append("<|im_end|>\n");
      formatted_prompt.append("<|im_start|>assistant\n");
      break;
    case example::DecoderModelVersion::kGlm:
      formatted_prompt.append("<|user|>\n");
      formatted_prompt.append(prompt);
      if (!system_prompt.empty()) {
        formatted_prompt.append("<|system|>\n");
        formatted_prompt.append(system_prompt);
      }
      formatted_prompt.append("<|assistant|>\n");
      break;
    default:
      ET_CHECK_MSG(false, "unsupported llama version");
      break;
  }
  return formatted_prompt;
}

// Reads one \n-terminated line from the pipe into `line`. Returns false if
// the client disconnected (ERROR_BROKEN_PIPE / ERROR_PIPE_NOT_CONNECTED).
bool ReadLine(HANDLE pipe, std::string& carry, std::string& line) {
  for (;;) {
    size_t nl = carry.find('\n');
    if (nl != std::string::npos) {
      line = carry.substr(0, nl);
      carry.erase(0, nl + 1);
      return true;
    }
    char buf[4096];
    DWORD bytes_read = 0;
    BOOL ok = ReadFile(pipe, buf, sizeof(buf), &bytes_read, nullptr);
    if (!ok || bytes_read == 0) {
      DWORD err = GetLastError();
      if (err == ERROR_BROKEN_PIPE || err == ERROR_PIPE_NOT_CONNECTED) {
        return false;
      }
      ET_LOG(Error, "ReadFile on pipe failed: %lu", err);
      return false;
    }
    carry.append(buf, bytes_read);
  }
}

bool WriteLine(HANDLE pipe, const std::string& line) {
  std::string out = line;
  out.push_back('\n');
  size_t total_written = 0;
  while (total_written < out.size()) {
    DWORD bytes_written = 0;
    BOOL ok = WriteFile(
        pipe,
        out.data() + total_written,
        static_cast<DWORD>(out.size() - total_written),
        &bytes_written,
        nullptr);
    if (!ok) {
      ET_LOG(Error, "WriteFile on pipe failed: %lu", GetLastError());
      return false;
    }
    total_written += bytes_written;
  }
  return true;
}

} // namespace

int main(int argc, char** argv) {
  QnnExecuTorchBackendRegister(
      reinterpret_cast<void*>(executorch::runtime::register_backend));

  gflags::ParseCommandLineFlags(&argc, &argv, true);

  std::unique_ptr<executorch::extension::Module> module =
      std::make_unique<executorch::extension::Module>(
          FLAGS_model_path.c_str(),
          executorch::extension::Module::LoadMode::MmapUseMlockIgnoreErrors);

  example::Runner runner(
      std::move(module),
      FLAGS_decoder_model_version.c_str(),
      FLAGS_model_path.c_str(),
      FLAGS_tokenizer_path.c_str(),
      /*dump_logits_path=*/"",
      FLAGS_performance_output_path.c_str(),
      FLAGS_temperature,
      FLAGS_eval_mode,
      FLAGS_shared_buffer,
      FLAGS_ngram,
      FLAGS_window,
      FLAGS_gcap,
      /*tokenizer=*/nullptr,
      /*attention_sink_rope_module=*/nullptr);

  auto decoder_model_version_res = runner.get_decoder_model_version();
  ET_CHECK_MSG(
      decoder_model_version_res.ok(), "Failed to get decoder model version");
  auto decoder_model_version = decoder_model_version_res.get();

  ET_LOG(Info, "Loading model and restoring QNN context...");
  auto load_err = runner.load();
  ET_CHECK_MSG(
      load_err == executorch::runtime::Error::Ok, "Failed to load model");
  ET_LOG(Info, "Model loaded. This cost is now paid once, not per-request.");

  std::string pipe_path = "\\\\.\\pipe\\" + FLAGS_pipe_name;
  ET_LOG(Info, "Listening on %s", pipe_path.c_str());

  bool shutdown_requested = false;
  while (!shutdown_requested) {
    HANDLE pipe = CreateNamedPipeA(
        pipe_path.c_str(),
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        PIPE_UNLIMITED_INSTANCES,
        /*out buffer=*/65536,
        /*in buffer=*/65536,
        0,
        nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
      ET_LOG(Error, "CreateNamedPipeA failed: %lu", GetLastError());
      return 1;
    }

    BOOL connected = ConnectNamedPipe(pipe, nullptr)
        ? TRUE
        : (GetLastError() == ERROR_PIPE_CONNECTED);
    if (!connected) {
      ET_LOG(Error, "ConnectNamedPipe failed: %lu", GetLastError());
      CloseHandle(pipe);
      continue;
    }
    ET_LOG(Info, "Client connected.");

    std::string carry;
    std::string line;
    while (ReadLine(pipe, carry, line)) {
      if (line.empty()) {
        continue;
      }
      json req;
      try {
        req = json::parse(line);
      } catch (const json::parse_error& e) {
        WriteLine(
            pipe,
            json{{"ok", false}, {"error", std::string("bad json: ") + e.what()}}
                .dump());
        continue;
      }

      if (req.value("cmd", "") == "shutdown") {
        WriteLine(pipe, json{{"ok", true}}.dump());
        shutdown_requested = true;
        break;
      }
      if (req.value("cmd", "") == "reset") {
        runner.reset();
        WriteLine(pipe, json{{"ok", true}, {"reset", true}}.dump());
        continue;
      }

      std::string prompt = req.value("prompt", "");
      if (prompt.empty()) {
        WriteLine(
            pipe, json{{"ok", false}, {"error", "prompt must be non-empty"}}.dump());
        continue;
      }
      std::string system_prompt = req.value("system_prompt", "");
      int32_t seq_len = req.value("seq_len", FLAGS_seq_len);
      float temperature =
          req.value("temperature", static_cast<float>(FLAGS_temperature));
      bool include_metrics = req.value("include_metrics", false);
      bool stream = req.value("stream", false);
      if (req.value("reset", false)) {
        runner.reset();
      }

      std::string formatted_prompt =
          get_formatted_prompt(prompt, system_prompt, decoder_model_version);

      std::string response_text;
      auto token_callback = [&](const std::string& piece) {
        response_text += piece;
        if (stream) {
          WriteLine(pipe, json{{"delta", piece}}.dump());
        }
      };

      executorch::extension::llm::GenerationConfig config{
          /*echo=*/false,
          /*grammar=*/"",
          /*grammar_type=*/"",
          /*ignore_eos=*/false,
          /*max_new_tokens=*/-1,
          /*warming=*/false,
          seq_len,
          temperature,
          /*num_bos=*/0,
          /*num_eos=*/0};

      runner.reset_stats();
      std::unique_ptr<executorch::llm::Stats> captured_stats;
      auto stats_callback = [&](const executorch::llm::Stats& s) {
        // Stats has a const member (SCALING_FACTOR_UNITS_PER_SECOND), so
        // copy-assignment is implicitly deleted; copy-construct instead.
        captured_stats = std::make_unique<executorch::llm::Stats>(s);
      };

      auto err = runner.generate_from_prompt_or_file(
          formatted_prompt,
          /*tokenized_prompt=*/false,
          config,
          token_callback,
          stats_callback);

      if (err != executorch::runtime::Error::Ok) {
        WriteLine(
            pipe,
            json{
                {"ok", false},
                {"error",
                 "generate failed with error " +
                     std::to_string(static_cast<uint32_t>(err))}}
                .dump());
        continue;
      }

      json resp{{"ok", true}, {"text", response_text}, {"done", true}};
      if (include_metrics && captured_stats) {
        resp["metrics"] = json::parse(
            executorch::extension::llm::stats_to_json_string(*captured_stats));
      }
      if (!WriteLine(pipe, resp.dump())) {
        break;
      }
    }

    ET_LOG(Info, "Client disconnected.");
    FlushFileBuffers(pipe);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
  }

  ET_LOG(Info, "Shutdown requested. Exiting.");
  return 0;
}
