"""Overlap external work while keeping all application operations on the caller thread."""
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue


def run_parallel(jobs, execute, dispatch, max_workers=4):
    """Run ``execute(job, call)`` concurrently and return results in job order.

    ``execute`` must not access thread-local application state (including Frappe).
    Its ``call(*args, **kwargs)`` runs ``dispatch`` on the thread that invoked this
    function, then returns its value or re-raises its exception in the worker.
    Permissions, database access, audit writes and shared budgets belong there.

    An uncaught worker exception is re-raised only after every job has finished,
    so other workers can still complete pending calls. The first failed job in
    submission order determines the exception. Network work needs its own timeout.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError('max_workers must be a positive integer')
    jobs = list(jobs)
    if not jobs:
        return []

    requests = Queue()
    completed = object()

    def call(*args, **kwargs):
        response = Future()
        requests.put((response, args, kwargs))
        return response.result()

    def finished(_future):
        requests.put(completed)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs)),
                            thread_name_prefix='agent-model') as pool:
        futures = [pool.submit(execute, job, call) for job in jobs]
        for future in futures:
            future.add_done_callback(finished)
        remaining = len(futures)
        interrupted = None
        while remaining:
            try:
                request = requests.get()
                if request is completed:
                    remaining -= 1
                    continue
                response, args, kwargs = request
                if interrupted is not None:
                    response.set_exception(RuntimeError('Parallel work was interrupted.'))
                    continue
                try:
                    response.set_result(dispatch(*args, **kwargs))
                except BaseException as error:
                    # Propagate application errors to the requesting worker. An
                    # error in one tool must not strand other workers in call().
                    response.set_exception(error)
            except BaseException as error:
                # A caller interruption during queue waiting must still drain
                # requests before executor shutdown waits for the workers.
                interrupted = interrupted or error
        if interrupted is not None:
            raise interrupted
    return [future.result() for future in futures]
