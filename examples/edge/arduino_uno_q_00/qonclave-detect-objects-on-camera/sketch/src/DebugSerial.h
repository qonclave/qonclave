#pragma once

// RouterBridge owns the MCU/Linux serial transport. Raw Serial output on the
// same channel corrupts its RPC packets, so diagnostics are disabled unless a
// dedicated debug firmware build opts in with:
//   -DQONCLAVE_MCU_DEBUG_SERIAL=1
#ifndef QONCLAVE_MCU_DEBUG_SERIAL
#define QONCLAVE_MCU_DEBUG_SERIAL 0
#endif

#if QONCLAVE_MCU_DEBUG_SERIAL
#define QONCLAVE_DEBUG_BEGIN(baud) Serial.begin(baud)
#define QONCLAVE_DEBUG(statement) do { statement; } while (0)
#else
#define QONCLAVE_DEBUG_BEGIN(baud) do { (void)(baud); } while (0)
#define QONCLAVE_DEBUG(statement) do { } while (0)
#endif
