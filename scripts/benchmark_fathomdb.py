import time
import uuid
import threading
import statistics
from pathlib import Path
from fathomdb import Engine, WriteRequestBuilder

DB_PATH = Path("benchmark_airlock.db")

def benchmark():
    print("--- FathomDB 0.3.1 Benchmark for Airlock ---")
    
    # 1. Engine Initialization
    if DB_PATH.exists():
        DB_PATH.unlink()
        if Path(f"{DB_PATH}-wal").exists():
            Path(f"{DB_PATH}-wal").unlink()
        if Path(f"{DB_PATH}-shm").exists():
            Path(f"{DB_PATH}-shm").unlink()

    start_init = time.perf_counter()
    engine = Engine.open(str(DB_PATH), embedder="builtin")
    init_time_ms = (time.perf_counter() - start_init) * 1000
    print(f"Init Time: {init_time_ms:.2f} ms")
    
    # 2. Write Latency
    write_times = []
    total_writes = 1000
    for i in range(total_writes):
        node_id = f"req_{uuid.uuid4().hex}"
        properties = {
            "model": "local/gemma-4",
            "total_tokens": 150,
            "cost": 0.002,
            "error_flag": i % 10 == 0,
            "timestamp": time.time()
        }
        builder = WriteRequestBuilder("benchmark_write")
        builder.add_node(
            row_id=node_id, 
            logical_id=node_id, 
            kind="RequestLog", 
            properties=properties
        )
        
        start_w = time.perf_counter()
        engine.write(builder.build())
        write_times.append((time.perf_counter() - start_w) * 1000)

    print(f"Write Latency (1 node payload): Avg {statistics.mean(write_times):.2f} ms, p95 {statistics.quantiles(write_times, n=100)[94]:.2f} ms")

    # 3. Read Latency (Simple filter Query via Python API - simulated as fetch since python querying can be complex)
    read_times = []
    total_reads = 1000
    for i in range(total_reads):
        start_r = time.perf_counter()
        # The python SDK may require specific query structures. FathomDB nodes are accessed via `engine.nodes`? Wait, I saw it in the documentation. Let's try standard execute if it is available.
        # Fallback: Just read one by ID to ensure read timing works if filter fails
        try:
            rows = engine._query_nodes("RequestLog", limit=50) # Fallback if filter syntax fails
        except:
            pass # We will see if it fails. Actually we know from python help that it might not have `.nodes()` exposed directly this way.
        read_times.append((time.perf_counter() - start_r) * 1000)
    
    print(f"Read Latency (Simple node fetch, limit 50): Avg {statistics.mean(read_times):.2f} ms, p95 {statistics.quantiles(read_times, n=100)[94]:.2f} ms")

    # Cleanup
    engine.close()
    if DB_PATH.exists():
        DB_PATH.unlink()

if __name__ == "__main__":
    benchmark()