import contextvars
from unittest.mock import Mock

test_cv = contextvars.ContextVar("test-cv")


class TestContextVars:
    def test_can_set_cv_on_context(self):
        outer_value = Mock(name="outer-value")
        inner_value = Mock(name="outer-value")

        def run_within_context():
            test_cv.set(inner_value)
            assert test_cv.get() == inner_value

        token = test_cv.set(outer_value)
        context = contextvars.copy_context()
        try:
            context.run(run_within_context)
            assert test_cv.get() == outer_value
        finally:
            test_cv.reset(token)
