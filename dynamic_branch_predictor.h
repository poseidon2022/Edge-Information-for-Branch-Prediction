#ifndef DYNAMIC_BRANCH_PREDICTOR_H
#define DYNAMIC_BRANCH_PREDICTOR_H

#include <stdint.h> // For uint64_t
#include <stdbool.h> // For bool

#ifdef __cplusplus
extern "C" {
#endif

// Function signature expected by the LLVM pass
void logBranchOutcome(uint64_t branchID, bool taken);

// Function to print/save collected dynamic features (call this at program exit)
void finalizeBranchPredictionData();

#ifdef __cplusplus
}
#endif

#endif // DYNAMIC_BRANCH_PREDICTOR_H