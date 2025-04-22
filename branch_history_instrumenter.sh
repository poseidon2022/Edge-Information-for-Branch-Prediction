#!/bin/bash

# Directory containing LLVM IR files (default: current directory, or specify with argument)
IR_DIR="dsa/dsa/llvm"

# Output directories
INSTR_DIR="instrumented_programs"
LOG_DIR="branch_history_logs"

# Create directories if they don't exist
for DIR in "$INSTR_DIR" "$LOG_DIR"; do
    if [ ! -d "$DIR" ]; then
        echo "Creating directory: $DIR"
        mkdir "$DIR"
        if [ $? -ne 0 ]; then
            echo "Failed to create directory $DIR"
            exit 1
        fi
    fi
done

# Compile the shared object for BranchHistoryInstrumenter
echo "Compiling BranchHistoryInstrumenter.so..."
clang -std=c++17 -fPIC -shared -o BranchHistoryInstrumenter.so BranchHistoryInstrumenter.cpp \
    $(llvm-config --cxxflags --ldflags --libs core analysis passes support) \
    -I/usr/lib/llvm-15/include \
    -Wl,-rpath,$(llvm-config --libdir)

if [ $? -ne 0 ]; then
    echo "Compilation of BranchHistoryInstrumenter.so failed"
    exit 1
fi

# Compile DynamicLog.cpp
echo "Compiling DynamicLog.o..."
clang -c -o DynamicLog.o DynamicLog.cpp

if [ $? -ne 0 ]; then
    echo "Compilation of DynamicLog.o failed"
    exit 1
fi

# Find all .ll files recursively
IR_FILES=($(find "$IR_DIR" -type f -name "*.ll"))
TOTAL_FILES=${#IR_FILES[@]}

if [ $TOTAL_FILES -eq 0 ]; then
    echo "No .ll files found in $IR_DIR"
    exit 1
fi

echo "Found $TOTAL_FILES LLVM IR files to process"

# Step 1: Instrument each IR file
for ((i = 0; i < TOTAL_FILES; i++)); do
    IR_FILE="${IR_FILES[$i]}"
    BASE_NAME=$(basename "$IR_FILE" .ll)
    INSTR_FILE="$INSTR_DIR/${BASE_NAME}_instrumented.ll"
    
    PROGRESS=$((i + 1))
    echo "Instrumenting $PROGRESS out of $TOTAL_FILES: $IR_FILE -> $INSTR_FILE"

    opt -load-pass-plugin=./BranchHistoryInstrumenter.so \
        -passes="branch-history-instrumenter" \
        -opaque-pointers "$IR_FILE" \
        -o "$INSTR_FILE"

    if [ $? -ne 0 ]; then
        echo "Instrumentation failed for $IR_FILE"
    elif [ ! -s "$INSTR_FILE" ]; then
        echo "Warning: $INSTR_FILE is empty for $IR_FILE"
    else
        echo "Successfully instrumented $IR_FILE"
    fi
done

# Step 2: Compile and run each instrumented file
INSTR_FILES=($(find "$INSTR_DIR" -type f -name "*_instrumented.ll"))
TOTAL_INSTR=${#INSTR_FILES[@]}

echo "Found $TOTAL_INSTR instrumented files to compile and run"

for ((i = 0; i < TOTAL_INSTR; i++)); do
    INSTR_FILE="${INSTR_FILES[$i]}"
    BASE_NAME=$(basename "$INSTR_FILE" _instrumented.ll)
    EXEC_FILE="${INSTR_DIR}/${BASE_NAME}_instrumented"
    LOG_FILE="$LOG_DIR/${BASE_NAME}_branch_history.log"
    
    PROGRESS=$((i + 1))
    echo "Compiling and running $PROGRESS out of $TOTAL_INSTR: $INSTR_FILE -> $EXEC_FILE"

    # Compile with DynamicLog.o
    clang "$INSTR_FILE" DynamicLog.o -o "$EXEC_FILE" -lstdc++

    if [ $? -ne 0 ]; then
        echo "Compilation failed for $INSTR_FILE"
        continue
    fi

    # Run the instrumented program with PROGRAM_NAME set
    echo "Running $EXEC_FILE..."
    PROGRAM_NAME="$BASE_NAME" ./"$EXEC_FILE"

    if [ $? -ne 0 ]; then
        echo "Execution failed for $EXEC_FILE"
    else
        echo "Successfully ran $EXEC_FILE"
        if [ -f "$LOG_FILE" ]; then
            echo "Log file created: $LOG_FILE"
        else
            echo "Warning: $LOG_FILE not found"
        fi
        
        # Run post-processing Python script
        # if [ -f "post_processing.py" ]; then
        #     echo "Running post_processing.py for $EXEC_FILE..."
        #     python3 post_processing.py
        #     if [ $? -ne 0 ]; then
        #         echo "Post-processing failed for $EXEC_FILE"
        #     else
        #         echo "Successfully post-processed $EXEC_FILE"
        #     fi
        # else
        #     echo "Warning: post_processing.py not found"
        # fi
    fi
done

echo "Done processing all $TOTAL_INSTR instrumented files"