import signal
import threading


def main() -> None:
    stopped = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        print(f"Worker menerima sinyal {signal.Signals(signum).name}; berhenti...", flush=True)
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    print("Worker siap; menunggu job (belum ada scheduler).", flush=True)
    stopped.wait()
    print("Worker berhenti dengan tertib.", flush=True)


if __name__ == "__main__":
    main()
