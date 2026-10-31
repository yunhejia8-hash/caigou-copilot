"""Model work overlaps; permission checks and application state stay on one thread."""
import threading
import unittest

from suke_ops.parallel_work import run_parallel


class ParallelWorkTests(unittest.TestCase):
    def test_jobs_overlap_but_dispatch_stays_on_calling_thread(self):
        caller = threading.get_ident()
        ready = threading.Barrier(4, timeout=3)
        worker_ids, dispatch_ids = set(), []

        def execute(job, call):
            worker_ids.add(threading.get_ident())
            ready.wait()
            return call('read', job, suffix='checked')

        def dispatch(kind, job, suffix):
            dispatch_ids.append(threading.get_ident())
            self.assertEqual(kind, 'read')
            return str(job) + '-' + suffix

        self.assertEqual(run_parallel(range(4), execute, dispatch),
                         ['0-checked', '1-checked', '2-checked', '3-checked'])
        self.assertEqual(len(worker_ids), 4)
        self.assertNotIn(caller, worker_ids)
        self.assertEqual(dispatch_ids, [caller] * 4)

    def test_results_keep_submission_order_when_jobs_finish_in_reverse(self):
        ready = threading.Barrier(3, timeout=3)
        finished = [threading.Event() for _ in range(3)]
        completion_order = []

        def execute(job, call):
            ready.wait()
            if job < 2:
                self.assertTrue(finished[job + 1].wait(3))
            result = call(job)
            finished[job].set()
            return result

        def dispatch(job):
            completion_order.append(job)
            return job * 10

        self.assertEqual(run_parallel(range(3), execute, dispatch), [0, 10, 20])
        self.assertEqual(completion_order, [2, 1, 0])

    def test_dispatch_exception_can_be_handled_by_worker_and_next_call_works(self):
        expected = PermissionError('denied')

        def execute(job, call):
            try:
                call('restricted')
            except PermissionError as error:
                self.assertIs(error, expected)
            return call('allowed', job=job)

        def dispatch(kind, job=None):
            if kind == 'restricted':
                raise expected
            return job

        self.assertEqual(run_parallel([7], execute, dispatch), [7])

    def test_uncaught_worker_failure_does_not_strand_other_rpc_requests(self):
        ready = threading.Barrier(3, timeout=3)
        completed = []
        expected = ValueError('model failed')

        def execute(job, call):
            ready.wait()
            if job == 0:
                raise expected
            call('started', job)
            return call('done', job)

        def dispatch(kind, job):
            completed.append((kind, job))
            return job

        with self.assertRaises(ValueError) as caught:
            run_parallel(range(3), execute, dispatch)
        self.assertIs(caught.exception, expected)
        self.assertCountEqual(completed, [('started', 1), ('done', 1), ('started', 2), ('done', 2)])

    def test_uncaught_dispatch_failure_waits_for_other_jobs(self):
        completed = []

        def execute(job, call):
            return call(job)

        def dispatch(job):
            if job == 0:
                raise PermissionError('denied')
            completed.append(job)
            return job

        with self.assertRaises(PermissionError):
            run_parallel(range(5), execute, dispatch, max_workers=2)
        self.assertCountEqual(completed, [1, 2, 3, 4])

    def test_empty_jobs_and_invalid_worker_limit(self):
        self.assertEqual(run_parallel([], None, None), [])
        for workers in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                run_parallel([], None, None, max_workers=workers)


if __name__ == '__main__':
    unittest.main()
