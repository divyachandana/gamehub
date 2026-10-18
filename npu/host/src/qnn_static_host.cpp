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
 * Persistent runner for a fixed-shape .pte (e.g. an encoder/classifier fully
 * delegated to QNN): loads the model and restores the QNN context once, then
 * serves inferences over stdin/stdout so a client pays ~ms per call instead of
 * a full model load.
 *
 * Usage: qnn_static_host <model.pte>
 *
 * Protocol (one line per message):
 *   startup -> "@@READY <n_inputs> <numel_0> ... <n_outputs> <numel_0> ..."
 *   request <- all inputs' elements, whitespace separated, inputs in method
 *              order, each flattened row-major (values are cast to the
 *              input's dtype: float/half/int32/int64/bool)
 *   reply   -> "@@OK <ms> <output_0 values...> <output_1 values...> ..."
 *              (outputs as float) or "@@ERR <message>"
 *   "quit"  -> exits.
 * Only lines starting with "@@" belong to the protocol; anything else on
 * stdout (backend logs) should be ignored by the client.
 */

#include <executorch/backends/qualcomm/runtime/QnnExecuTorch.h>
#include <executorch/extension/module/module.h>
#include <executorch/extension/tensor/tensor.h>
#include <executorch/runtime/backend/interface.h>
#include <executorch/runtime/platform/log.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

using executorch::aten::ScalarType;
using executorch::aten::SizesType;
using executorch::extension::from_blob;
using executorch::extension::Module;
using executorch::extension::TensorPtr;
using executorch::runtime::EValue;
using executorch::runtime::Error;

namespace {

struct Input {
  ScalarType dtype;
  std::vector<SizesType> sizes;
  size_t numel = 1;
  std::vector<uint8_t> data;
  TensorPtr tensor;
};

size_t elem_size(ScalarType t) {
  switch (t) {
    case ScalarType::Float:
    case ScalarType::Int:
      return 4;
    case ScalarType::Long:
      return 8;
    case ScalarType::Half:
      return 2;
    case ScalarType::Bool:
    case ScalarType::Byte:
    case ScalarType::Char:
      return 1;
    default:
      return 0;
  }
}

// IEEE fp32 -> fp16 (round to nearest), enough for model inputs.
uint16_t to_half(float f) {
  uint32_t x;
  std::memcpy(&x, &f, 4);
  uint32_t sign = (x >> 16) & 0x8000, mant = x & 0x7fffff;
  int32_t exp = static_cast<int32_t>((x >> 23) & 0xff) - 127 + 15;
  if (exp <= 0) return static_cast<uint16_t>(sign);
  if (exp >= 31) return static_cast<uint16_t>(sign | 0x7c00);
  return static_cast<uint16_t>(sign | (exp << 10) | ((mant + 0x1000) >> 13));
}

float from_half(uint16_t h) {
  uint32_t sign = (h & 0x8000) << 16, exp = (h >> 10) & 0x1f, mant = h & 0x3ff;
  uint32_t x = exp == 0 ? sign : sign | ((exp - 15 + 127) << 23) | (mant << 13);
  float f;
  std::memcpy(&f, &x, 4);
  return f;
}

void store(Input& in, size_t i, double v) {
  uint8_t* p = in.data.data();
  switch (in.dtype) {
    case ScalarType::Float:
      reinterpret_cast<float*>(p)[i] = static_cast<float>(v);
      break;
    case ScalarType::Half:
      reinterpret_cast<uint16_t*>(p)[i] = to_half(static_cast<float>(v));
      break;
    case ScalarType::Int:
      reinterpret_cast<int32_t*>(p)[i] = static_cast<int32_t>(v);
      break;
    case ScalarType::Long:
      reinterpret_cast<int64_t*>(p)[i] = static_cast<int64_t>(v);
      break;
    default:
      p[i] = static_cast<uint8_t>(v);
  }
}

} // namespace

int main(int argc, char** argv) {
  QnnExecuTorchBackendRegister(
      reinterpret_cast<void*>(executorch::runtime::register_backend));
  if (argc < 2) {
    std::printf("@@ERR usage: qnn_static_host <model.pte>\n");
    return 1;
  }

  Module module(argv[1], Module::LoadMode::MmapUseMlockIgnoreErrors);
  if (module.load_method("forward") != Error::Ok) {
    std::printf("@@ERR failed to load method forward from %s\n", argv[1]);
    return 1;
  }
  auto meta = module.method_meta("forward");
  ET_CHECK_MSG(meta.ok(), "no method meta");

  std::vector<Input> inputs(meta->num_inputs());
  std::string ready = "@@READY " + std::to_string(inputs.size());
  for (size_t i = 0; i < inputs.size(); ++i) {
    auto ti = meta->input_tensor_meta(i);
    ET_CHECK_MSG(ti.ok(), "input %zu is not a tensor", i);
    Input& in = inputs[i];
    in.dtype = ti->scalar_type();
    for (auto s : ti->sizes()) {
      in.sizes.push_back(s);
      in.numel *= s;
    }
    size_t es = elem_size(in.dtype);
    ET_CHECK_MSG(es > 0, "unsupported dtype for input %zu", i);
    in.data.assign(in.numel * es, 0);
    in.tensor = from_blob(in.data.data(), in.sizes, in.dtype);
    ready += " " + std::to_string(in.numel);
  }
  ready += " " + std::to_string(meta->num_outputs());
  for (size_t i = 0; i < meta->num_outputs(); ++i) {
    auto to = meta->output_tensor_meta(i);
    size_t n = 1;
    if (to.ok()) {
      for (auto s : to->sizes()) n *= s;
    }
    ready += " " + std::to_string(n);
  }
  std::printf("%s\n", ready.c_str());
  std::fflush(stdout);

  std::string line;
  while (std::getline(std::cin, line)) {
    if (line == "quit") break;
    if (line.empty()) continue;
    const char* p = line.c_str();
    char* end = nullptr;
    bool bad = false;
    for (auto& in : inputs) {
      for (size_t i = 0; i < in.numel; ++i) {
        double v = std::strtod(p, &end);
        if (end == p) {
          bad = true;
          break;
        }
        store(in, i, v);
        p = end;
      }
      if (bad) break;
    }
    if (bad) {
      std::printf("@@ERR expected more input values\n");
      std::fflush(stdout);
      continue;
    }

    std::vector<EValue> evs;
    for (auto& in : inputs) evs.emplace_back(in.tensor);
    auto t0 = std::chrono::steady_clock::now();
    auto res = module.execute("forward", evs);
    double ms = std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - t0)
                    .count();
    if (!res.ok()) {
      std::printf("@@ERR execute failed 0x%x\n", static_cast<unsigned>(res.error()));
      std::fflush(stdout);
      continue;
    }
    std::string out = "@@OK " + std::to_string(ms);
    char buf[32];
    for (auto& ev : *res) {
      if (!ev.isTensor()) continue;
      const auto& t = ev.toTensor();
      for (ssize_t i = 0; i < t.numel(); ++i) {
        float v = t.scalar_type() == ScalarType::Half
            ? from_half(t.const_data_ptr<uint16_t>()[i])
            : t.scalar_type() == ScalarType::Float ? t.const_data_ptr<float>()[i]
                                                   : 0.0f;
        std::snprintf(buf, sizeof(buf), " %.6g", v);
        out += buf;
      }
    }
    std::printf("%s\n", out.c_str());
    std::fflush(stdout);
  }
  return 0;
}
