#define NEEDS_PY_IDENTIFIER
#include <Python.h>
#include "cimpl.h"
#include "frameobject.h"
#include "structmember.h"


/* The only way to import `throw` method identifier, which is fast */
_Py_IDENTIFIER(throw);

/* UTILS */
PyObject* _import_object(const char* module_name, const char* cls_name) {
    PyObject* module = PyImport_ImportModule(module_name);
    if (module == NULL)
        return NULL;
    
    PyObject* object = PyObject_GetAttrString(module, cls_name);
    Py_DECREF(module);
    return object;
}

PyTypeObject* _import_cls(const char* module_name, const char* cls_name) {
    PyObject* cls = _import_object(module_name, cls_name);
    if (cls == NULL)
        return NULL;

    if (!PyType_Check(cls)) {
        Py_DECREF(cls);
        PyErr_Format(PyExc_TypeError, "Expect `%s.%s` to be a type", module_name, cls_name);
        return NULL;
    }

    return (PyTypeObject*) cls;
}

enum CoroState get_coroutine_state(PyCoroObject* coroutine) {
    if (coroutine->cr_frame != NULL && coroutine->cr_frame->f_state == FRAME_EXECUTING)
        return CORO_RUNNING;
    else if (coroutine->cr_frame == NULL)
        return CORO_CLOSED;
    else if (coroutine->cr_frame->f_lasti == -1)
        return CORO_CREATED;
    else
        return CORO_SUSPENDED;
}

const char* coro_state_to_str(enum CoroState coro_state) {
    switch (coro_state) {
        case CORO_CREATED:
            return "CORO_CREATED";
        case CORO_RUNNING:
            return "CORO_RUNNING";
        case CORO_SUSPENDED:
            return "CORO_SUSPENDED";
        case CORO_CLOSED:
            return "CORO_CLOSED";
        default:
            return "UNKNOWN";
    }
}

#define _BEFORE_WARN() \
    PyObject* exc_type = NULL; \
    PyObject* exc = NULL; \
    PyObject* tb = NULL; \
    if (PyErr_Occurred()) { PyErr_Fetch(&exc_type, &exc, &tb); PyErr_Clear(); }

#define _AFTER_WARN() \
    if (exc_type != NULL) PyErr_Restore(exc_type, exc, tb);


/* AIO Objects and tools */
PyObject* _aio_abc_event_loop_cls = NULL;
PyObject* _aio_abc_future_cls = NULL;
PyObject* _aio_future_states_enum = NULL;

PyObject* _aio_cancelled_exc_cls = NULL;
PyObject* _aio_fut_not_ready_exc_cls = NULL;
PyObject* _aio_fut_finished_exc_cls = NULL;
PyObject* _aio_self_cancel_forbidden_exc_cls = NULL;
PyObject* _aio_already_cancalling_exc_cls = NULL;

PyObject* _aio_current_task_cv = NULL;


PyObject* _aio_fut_get_state(const char* state_name) {
    return PyObject_GetAttrString(_aio_future_states_enum, state_name);
}

int _is_aio_abc_future(PyObject* fut) {
    return PyObject_IsInstance(fut, _aio_abc_future_cls);    
}

int _aio_future_cancel(PyObject* fut, PyObject* cancellation) {
    if (_is_aio_abc_future(fut) != 1) {
        PyErr_Format(PyExc_TypeError, "Can't cancell non-aio-future object %R", fut);
        return -1;
    }
    if (PyObject_IsInstance(cancellation, _aio_cancelled_exc_cls) != 1) {
        PyErr_Format(PyExc_TypeError, "`aio.Cancelled` instance excepced, not %R", fut);
        return -1;
    }

    PyObject* cancel_future = _import_object("aio.future._factories", "cancel_future");
    if (cancel_future == NULL)
        return -1;

    PyObject* call_args = Py_BuildValue("(OO)", fut, cancellation);
    if (call_args == NULL) {
        Py_DECREF(cancel_future);
        return -1;
    }

    PyObject* res = PyObject_Call(cancel_future, call_args, NULL);
    Py_DECREF(call_args);
    Py_DECREF(cancel_future);
    if (res == NULL)
        return -1;

    Py_DECREF(res);
    return 1;
}

PyObject* _aio_abc_future_call_method(PyObject* fut, const char* name, PyObject* arg) {
    if (_is_aio_abc_future(fut) != 1) {
        PyErr_Format(PyExc_TypeError, "Can't call method '%s' non-aio-future object %R", name, fut);
        return NULL;
    }

    return PyObject_CallMethod(fut, name, "(O)", arg);
}

int _aio_abc_future_is_finished(PyObject* fut) {
    if (_is_aio_abc_future(fut) != 1) {
        PyErr_Format(PyExc_TypeError, "Can't cancell non-aio-future object %R", fut);
        return -1;
    }

    PyObject* res = PyObject_GetAttrString(fut, "is_finished");
    if (res == NULL)
        return -1;
    
    int is_finished;
    if (Py_IsTrue(res))
        is_finished = 1;
    else if (Py_IsFalse(res))
        is_finished = 0;
    else {
        PyErr_Format(PyExc_TypeError, "`aio.Future.is_finished` must return boolean value, not %R", res);
        is_finished = -1;
    }
    Py_DECREF(res);
    return is_finished;
}

void _fut_raise_unexpected_state(void) {
    PyErr_SetString(PyExc_RuntimeError, "Unexpected future state");
}

/* Future CLASS DEFINITION */
PyObject* future_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
    Future* self = (Future*) type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;

    self->state = pending;
    self->data.pending.result_callbacks = NULL;
    self->loop = NULL;
    self->label = NULL;

    return (PyObject*) self;
}

int _future_init_impl(Future* self, PyObject* loop, PyObject* label) {
    if (loop) {
        int el_check = PyObject_IsInstance(loop, _aio_abc_event_loop_cls);
        if (el_check == -1) {
            return -1;
        } else if (el_check == 0) {
            PyObject* type = PyObject_Type(loop);
            if (type == NULL)
                return -1;

            PyErr_Format(
                PyExc_TypeError,
                "argument 1 must be `aio.EventLoop` instance, not %s",
                ((PyTypeObject*) type)->tp_name
            );
            return -1;
        }

        Py_XDECREF(self->loop);
        self->loop = Py_NewRef(loop);
    }
    if (label) {
        Py_XDECREF(self->label);
        self->label = Py_NewRef(label);
    }

    PyObject* res_cbs = PySet_New(NULL);
    if (res_cbs == NULL)
        return -1;

    self->data.pending.result_callbacks = Py_NewRef(res_cbs);
    return 0;
}

int future_init(Future* self, PyObject* args, PyObject* kwds) {
    PyObject* loop = NULL;
    PyObject* label = NULL;
    int parse_result = PyArg_ParseTuple(args, "OU", &loop, &label);
    if (!parse_result)
        return -1;

    return _future_init_impl(self, loop, label);
}

void _future_emit_warnings(Future* self) {
    if (self->state == failed && !self->data.failed.exc_retrieved) {
        if (1) {
            _BEFORE_WARN();
            PyErr_WarnFormat(
                PyExc_UserWarning,
                2,
                "Feature `%R` is about to be destroyed, but her exception %R was never " \
                "retrieved. Please, `await` this feature, or call `result` or " \
                "`exception` methods to prevent exception being ignored silently.",
                self,
                self->data.failed.exc
            );
            _AFTER_WARN();
        } else {
            _BEFORE_WARN();
            PyObject* fut_exc = self->data.failed.exc;
            PyErr_SetObject(PyExceptionInstance_Class(fut_exc), fut_exc);
            PyErr_WriteUnraisable((PyObject*) self);
            PyErr_Clear();
            _AFTER_WARN();
        }
    }
    if (self->state == pending) {
        _BEFORE_WARN();
        PyErr_WarnFormat(
            PyExc_UserWarning,
            2,
            "Feature `%R` is about to be destroyed, but not finished, that normally should never occur.",
            self
        );
        _AFTER_WARN();
    }
}

void _future_destroy_impl(Future* self) {
    _future_emit_warnings(self);

    Py_XDECREF(self->loop);
    Py_XDECREF(self->label);

    switch (self->state) {
        case pending:
            Py_XDECREF(self->data.pending.result_callbacks);
            break;
        case success:
            Py_XDECREF(self->data.success.result);
            Py_XDECREF(self->data.success.scheduled_cbs);
            break;
        case failed:
            Py_XDECREF(self->data.failed.exc);
            Py_XDECREF(self->data.failed.scheduled_cbs);
            break;
    }
}

void future_destroy(Future* self) {
    _future_destroy_impl(self);
    Py_TYPE(self)->tp_free((PyObject*) self);
}

PyObject* _repr_label_and_state(Future* self) {
    PyObject* state = future_state_getter(self, NULL);
    if (state == NULL)
        return NULL;

    PyObject* state_name = PyObject_GetAttrString(state, "name");
    Py_DECREF(state);
    if (state == NULL)
        return NULL;

    int clear_label = 0;
    PyObject* label = self->label;
    if (label == NULL) {
        label = PyUnicode_FromString("UNNAMED");
        clear_label = 1;
    }
    PyObject* res = PyUnicode_FromFormat("label=\"%U\" state=%U", label, state_name);
    if (clear_label)
        Py_DECREF(label);
    Py_DECREF(state_name);
    return res;
}

PyObject* future_repr(Future* self) {
    PyObject* label_and_state = _repr_label_and_state(self);
    if (label_and_state == NULL)
        return NULL;

    PyObject* res = PyUnicode_FromFormat("<Future %U>", label_and_state);
    Py_DECREF(label_and_state);
    return res;
}

PyObject* future_state_getter(Future* self, void* closure) {
    switch (self->state) {
        case pending:
            return _aio_fut_get_state("running");
        case success:
        case failed:
            return _aio_fut_get_state("finished");
            break;
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }
}

PyObject* future_result(Future* self, PyObject* _) {
    PyObject* exc;
    switch (self->state) {
        case pending:
            PyErr_SetString(_aio_fut_not_ready_exc_cls, "Still no result");
            return NULL;
        case success:
            return Py_NewRef(self->data.success.result);
        case failed:
            exc = self->data.failed.exc;
            PyErr_SetObject(PyExceptionInstance_Class(exc), exc);
            return NULL;
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }
}

PyObject* future_exception(Future* self, PyObject* _) {
    switch (self->state) {
        case pending:
            PyErr_SetString(_aio_fut_not_ready_exc_cls, "Still no result");
            return NULL;
        case success:
            return Py_NewRef(Py_None);
        case failed:
            self->data.failed.exc_retrieved = 1;
            return Py_NewRef(self->data.failed.exc);
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }
}

PyObject* future_add_callback(Future* self, PyObject* cb, PyObject* _) {
    if (!PyCallable_Check(cb)) {
        PyErr_SetString(PyExc_TypeError, "Callback must be callable object");
        return NULL;
    }

    switch (self->state) {
        case pending:
            if (PySet_Add(self->data.pending.result_callbacks, cb) == -1)
                return NULL;
            else
                return Py_NewRef(Py_None);
        case success:
        case failed:
            PyErr_SetString(_aio_fut_finished_exc_cls, "Could not schedule callback for already finished future");
            return NULL;
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }
}

int _is_key_or_value_error(void) {
    return PyErr_Occurred() && (PyErr_ExceptionMatches(PyExc_ValueError) || PyErr_ExceptionMatches(PyExc_KeyError));
}

int _is_key_error(void) {
    return PyErr_Occurred() && PyErr_ExceptionMatches(PyExc_ValueError);
}

PyObject* future_remove_callback(Future* self, PyObject* cb, PyObject* _) {
    PyObject* sched_cbs;

    switch (self->state) {
        case pending:
            if (PySet_Discard(self->data.pending.result_callbacks, cb) == -1) {
                if (!_is_key_or_value_error()) {
                    return NULL;
                }
                PyErr_Clear();
            }
            return Py_NewRef(Py_None);
        case success:
            sched_cbs = self->data.success.scheduled_cbs;
            break;
        case failed:
            sched_cbs = self->data.failed.scheduled_cbs;
            break;
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }

    PyObject* handle = PyDict_GetItemWithError(sched_cbs, cb);
    if (handle == NULL) {
        if (PyErr_Occurred() != NULL && !_is_key_error()) {
            return NULL;
        } else {
            PyErr_Clear();
            return Py_NewRef(Py_None);
        }
    }
    if (PyDict_DelItem(sched_cbs, cb) == -1)
        return NULL;

    return PyObject_CallMethod(handle, "cancel", (const char*) NULL);
}

PyObject* _schedule_callbacks(Future* self) {
    assert(self->state == pending);

    PyObject* scheduled = PyDict_New();

    PyObject* cbs_iter = PyObject_GetIter(self->data.pending.result_callbacks);
    PyObject* cb;
    if (cbs_iter == NULL)
        return NULL;

    PyObject* sched_res;
    while ((cb = PyIter_Next(cbs_iter))) {
        sched_res = PyObject_CallMethod(self->loop, "call_soon", "OO", cb, (PyObject*) self);
        if (sched_res == NULL) {
            Py_DECREF(cb);
            break;
        }

        PyDict_SetItem(scheduled, cb, sched_res);
    }

    Py_DECREF(cbs_iter);
    if (PyErr_Occurred()) {
        Py_DECREF(scheduled);
        return NULL;
    }
    return scheduled;
}

PyObject* _before_change_state(Future* self) {
    if (self->state != pending) {
        PyErr_SetString(_aio_fut_finished_exc_cls, "Future already finished");
        return NULL;
    }

    PyObject* scheduled = _schedule_callbacks(self);
    if (scheduled == NULL)
        return NULL;
    
    Py_DECREF(self->data.pending.result_callbacks);
    return scheduled;
}

PyObject* future_set_result(Future* self, PyObject* res, PyObject* _) {
    PyObject* scheduled = _before_change_state(self);
    if (scheduled == NULL)
        return NULL;

    self->state = success;
    self->data.success.result = Py_NewRef(res);
    self->data.success.scheduled_cbs = scheduled;

    return Py_NewRef(Py_None);
}

PyObject* future_set_exception(Future* self, PyObject* exc, PyObject* _) {
    switch (PyObject_IsInstance(exc, PyExc_BaseException)) {   
        case -1:
            return NULL;
        case 0:
            PyErr_SetString(PyExc_TypeError, "Future exception must be instance of `BaseException`");
            return NULL;
    }

    PyObject* scheduled = _before_change_state(self);
    if (scheduled == NULL) {
        return NULL;
    }
    
    self->state = failed;
    self->data.failed.exc = Py_NewRef(exc);
    self->data.failed.exc_retrieved = 0;
    self->data.failed.scheduled_cbs = scheduled;

    return Py_NewRef(Py_None);
}

PyObject* future_is_finished_getter(Future* self, void* closure) {
    switch (self->state) {
        case pending:
            return Py_False;
        case success:
        case failed:
            return Py_True;
        default:
            _fut_raise_unexpected_state();
    }
    return NULL;
}

PyObject* future_is_cancelled_getter(Future* self, void* closure) {
    switch (self->state) {
        case pending:
        case success:
            return Py_False;
        case failed:
            if (PyObject_IsInstance(self->data.failed.exc, _aio_cancelled_exc_cls))
                return Py_True;
            else
                return Py_False;
        default:
            _fut_raise_unexpected_state();
    }
    return NULL;
}

PyObject* future_await(Future* self, PyObject* _) {
    PyObject* args = Py_BuildValue("(O)", (PyObject*) self);
    if (args == NULL)
        return NULL;
    
    PyObject* new = PyObject_Call((PyObject*) &FutureAwaitIterType, args, NULL);
    Py_DECREF(args);
    return new;
}

static PyMemberDef future_members[] = {
    {"loop", T_OBJECT, offsetof(Future, loop), READONLY, "Future event loop"},
    {"label", T_OBJECT, offsetof(Future, label), READONLY, "Future label"},
    {NULL}
};

PyGetSetDef future_getset[] = {
    {"state", (getter) future_state_getter, NULL, "Current future state", NULL},
    {"is_finished", (getter) future_is_finished_getter, NULL, "Is future finished", NULL},
    {"is_cancelled", (getter) future_is_cancelled_getter, NULL, "Is future finished due to cancellation", NULL},
    {NULL}
};

static PyMethodDef future_methods[] = {
    {"result", (PyCFunction) future_result, METH_NOARGS, "Get future result or raise `aio.FutureNotReady`"},
    {"exception", (PyCFunction) future_exception, METH_NOARGS, "Get future exception or raise `aio.FutureNotReady`"},
    {
        "add_callback",
        (PyCFunction) future_add_callback,
        METH_O,
        "Get future exception or raise `aio.FutureFinishedError`"
    },
    {
        "remove_callback",
        (PyCFunction) future_remove_callback,
        METH_O,
        "Get future exception, or try to cancel already scheduled callback"
    },
    {
        "_set_result",
        (PyCFunction) future_set_result,
        METH_O,
        "Set future result and schedule pending callbacks or raise `aio.FutureFinishedError`"
    },
    {
        "_set_exception",
        (PyCFunction) future_set_exception,
        METH_O,
        "Set future exception and schedule pending callbacks or raise `aio.FutureFinishedError`"
    },
    {NULL}
};

static PyAsyncMethods future_async_methods = {
    .am_await = (unaryfunc) future_await
};

static PyTypeObject FutureType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "aio.future.cimpl.Future",
    .tp_doc = PyDoc_STR("Future C implementation"),
    .tp_basicsize = sizeof(Future),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = future_new,
    .tp_init = (initproc) future_init,
    .tp_dealloc = (destructor) future_destroy,
    .tp_members = future_members,
    .tp_methods = future_methods,
    .tp_getset = future_getset,
    .tp_as_async = &future_async_methods,
    .tp_repr = (reprfunc) future_repr
};


/* FutureAwaitIter CLASS DEFINITION */
PyObject* future_await_iter_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
    FutureAwaitIter* self = (FutureAwaitIter*) type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;

    self->fut = NULL;
    self->first_send_occurred = 0;
    return (PyObject*) self;
}

int future_await_iter_init(FutureAwaitIter* self, PyObject* args, PyObject* kwds) {
    PyObject* fut = NULL;
    if (!PyArg_ParseTuple(args, "O!", &FutureType, &fut))
        return -1;

    self->fut = (Future*) Py_NewRef(fut);
    return 0;
}

void future_await_iter_destroy(FutureAwaitIter* self) {
    Py_XDECREF(self->fut);
}

PyObject* future_await_iter_next(FutureAwaitIter* self) {
    if (self->fut->state == pending) {
        if (self->first_send_occurred) {
            PyErr_SetString(PyExc_RuntimeError, "Future being resumed after first yield, but still not finished!");
            return NULL;
        }

        self->first_send_occurred = 1;
        Py_INCREF((PyObject*) self->fut);
        return (PyObject*) self->fut;
    }

    PyObject* result = future_result(self->fut, NULL);
    if (PyErr_Occurred())
        return NULL;

    PyErr_SetObject(PyExc_StopIteration, result);
    return NULL;
}

PyObject* future_await_iter_send(FutureAwaitIter* self, PyObject* value) {
    return future_await_iter_next(self);
}

PyObject* future_await_iter_throw(FutureAwaitIter* self, PyObject* const* args, Py_ssize_t nargs) {
    /* Stolen from `asyncio.Future` C implementation */
    PyObject* type;
    PyObject* val = NULL;
    PyObject* tb = NULL;
    if (!_PyArg_CheckPositional("throw", nargs, 1, 3))
        return NULL;

    type = args[0];
    if (nargs == 3) {
        val = args[1];
        tb = args[2];
    } else if (nargs == 2) {
        val = args[1];
    }

    if (tb != NULL && !Py_IsNone(tb) && !PyTraceBack_Check(tb)) {
        PyErr_SetString(PyExc_TypeError, "throw() third argument must be a traceback");
        return NULL;
    }

    Py_INCREF(type);
    Py_XINCREF(val);
    Py_XINCREF(tb);

    if (PyExceptionClass_Check(type)) {
        PyErr_NormalizeException(&type, &val, &tb);
    } else if (PyExceptionInstance_Check(type)) {
        if (val) {
            PyErr_SetString(
                PyExc_TypeError,
                "instance exception may not have a separate value"
            );
            goto fail;
        }
        val = type;
        type = PyExceptionInstance_Class(type);
        Py_INCREF(type);
        if (tb == NULL)
            tb = PyException_GetTraceback(val);
    } else {
        PyErr_SetString(
            PyExc_TypeError, "exceptions must be classes deriving BaseException or instances of such a class"
        );
        goto fail;
    }

    PyErr_Restore(type, val, tb);
    return NULL;

fail:
    Py_DECREF(type);
    Py_XDECREF(val);
    Py_XDECREF(tb);
    return NULL;
}

PyObject* future_await_iter_close(FutureAwaitIter* self, PyObject* arg) {
    return Py_NewRef(Py_None);
}

static PyMethodDef future_await_iter_methods[] = {
    {"send",  (PyCFunction) future_await_iter_send, METH_O, NULL},
    {"throw", (PyCFunction) (void(*)(void)) future_await_iter_throw, METH_FASTCALL, NULL},
    {"close", (PyCFunction) future_await_iter_close, METH_NOARGS, NULL},
    {NULL}
};

static PyTypeObject FutureAwaitIterType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "aio.future.cimpl.FutureAwaitIter",
    .tp_doc = PyDoc_STR("Future.__await__ C implementation"),
    .tp_basicsize = sizeof(FutureAwaitIter),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = future_await_iter_new,
    .tp_init = (initproc) future_await_iter_init,
    .tp_dealloc = (destructor) future_await_iter_destroy,
    .tp_methods = future_await_iter_methods,
    .tp_iter = PyObject_SelfIter,
    .tp_iternext = (iternextfunc) future_await_iter_next
};


/* Task implementation */
PyObject* task_new(PyTypeObject* type, PyObject* args, PyObject* kwds) {
    Task* self = (Task*) type->tp_alloc(type, 0);
    if (self == NULL)
        return NULL;

    self->coroutine = NULL;
    self->step_fn_as_callback = NULL;
    self->state = TASK_CREATED;

    self->super.state = pending;
    self->super.data.pending.result_callbacks = NULL;
    self->super.loop = NULL;
    self->super.label = NULL;

    return (PyObject*) self;
}

int task_init(Task* self, PyObject* args, PyObject* kwds) {
    PyObject* coroutine = NULL;
    PyObject* loop = NULL;
    PyObject* label = NULL;

    if (!PyArg_ParseTuple(args, "OOU", &coroutine, &loop, &label))
        return -1;

    if (_future_init_impl(&self->super, loop, label))
        return -1;

    if (coroutine != NULL) {
        if (!PyCoro_CheckExact(coroutine)) {
            PyErr_SetString(PyExc_TypeError, "`coroutine` argument must be a coroutine object");
            return -1;
        }

        Py_XDECREF(self->coroutine);
        self->coroutine = (PyCoroObject*) Py_NewRef(coroutine);
    }
    if (self->step_fn_as_callback == NULL) {
        PyObject* step_fn = PyObject_GetAttrString((PyObject*) self, "_step");
        if (step_fn == NULL)
            return -1;
        self->step_fn_as_callback = step_fn;
    }
    
    Py_XDECREF(self->context);
    self->context = PyContext_CopyCurrent();

    return 0;
}

void _task_clear_current_state(Task* self) {
    switch (self->state) {
        case TASK_FUT_STATE:
        case TASK_CREATED:
            break;
        case TASK_SCHEDULED:
            Py_XDECREF(self->data.scheduled.handle);
            break;
        case TASK_RUNNING_STATE:
            Py_XDECREF(self->data.running.waiting_on);
            break;
        case TASK_CANCELLING:
            Py_XDECREF(self->data.cancelling.handle);
            Py_XDECREF(self->data.cancelling.inner_cancellation);
            break;
    }
}

void task_destroy(Task* self) {
    _future_destroy_impl((Future*) self);

    if (self->coroutine != NULL && self->coroutine->cr_frame != NULL) {
        _BEFORE_WARN();
        PyErr_WarnFormat(
            PyExc_UserWarning,
            2,
            "Task %R is about to be destroyed, but her coroutine hasn't been closed",
            self
        );
        _AFTER_WARN();
    }

    Py_XDECREF(self->coroutine);
    Py_XDECREF(self->step_fn_as_callback);
    Py_XDECREF(self->context);
    _task_clear_current_state(self);

    Py_TYPE(self)->tp_free((PyObject*) self);
}

PyObject* task_repr(Task* self) {
    PyObject* label_and_state = _repr_label_and_state((Future*) self);
    if (label_and_state == NULL)
        return NULL;

    PyObject* coroutine;
    if (self->coroutine != NULL)
        coroutine = (PyObject*) self->coroutine;
    else
        coroutine = Py_None;

    PyObject* res = PyUnicode_FromFormat("<Task %U %R>", label_and_state, coroutine);
    Py_DECREF(label_and_state);
    return res;
}

PyObject* task_state_getter(Task* self, void* closure) {
    switch (self->state) {
        case TASK_CREATED:
            return _aio_fut_get_state("created");
        case TASK_SCHEDULED:
            return _aio_fut_get_state("scheduled");
        case TASK_RUNNING_STATE:
            return _aio_fut_get_state("running");
        case TASK_CANCELLING:
            return _aio_fut_get_state("cancelling");
        case TASK_FUT_STATE:
            return future_state_getter((Future*) self, NULL);
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }
}

int _task_validate_can_go_to_state(Task* self, enum TaskState new_state) {
    assert(self->coroutine);

    enum CoroState coro_state = get_coroutine_state(self->coroutine);
    int expect_coro_states = 0;
    switch (new_state) {
        case TASK_CREATED:
        case TASK_SCHEDULED:
            expect_coro_states = CORO_CREATED;
            break;
        case TASK_RUNNING_STATE:
            expect_coro_states = CORO_SUSPENDED;
            break;
        case TASK_CANCELLING:
            expect_coro_states = CORO_SUSPENDED | CORO_CREATED;
            break;
        case TASK_FUT_STATE:
            expect_coro_states = CORO_CLOSED;
            break;
        default:
            _fut_raise_unexpected_state();
            return -1;
    }

    if ((coro_state & expect_coro_states) == 0) {
        assert(expect_coro_states != 0);

        enum CoroState coro_states[] = { CORO_CREATED, CORO_SUSPENDED, CORO_RUNNING, CORO_CLOSED };
        char joined_expec_states[80];
        int first = 1;
        for (ulong i = 0; i < sizeof(coro_state) / sizeof(enum CoroState); i++) {
            if (first)
                first = 0;
            else
                sprintf(joined_expec_states, ",");

            enum CoroState test_state = coro_states[i];
            if ((expect_coro_states & test_state) == test_state)
                sprintf(joined_expec_states, "%s", coro_state_to_str(test_state));
        }

        PyErr_Format(
            PyExc_RuntimeError,
            "Inconsistent task state: coroutine in state %s, but %s expected",
            coro_state_to_str(coro_state),
            joined_expec_states
        );
        return -1;
    }

    return 0;
}

void _task_do_go_to_state(Task* self, enum TaskState new_state, _TaskData new_data) {
    assert(self->coroutine);
    assert(_task_validate_can_go_to_state(self, new_state) != -1);

    _task_clear_current_state(self);
    self->data = new_data;
    self->state = new_state;

    switch (self->state) {
        case TASK_CREATED:
        case TASK_SCHEDULED:
        case TASK_RUNNING_STATE:
        case TASK_CANCELLING:
            assert(self->super.state == pending);
            break;
        case TASK_FUT_STATE:
            assert(self->super.state == success || self->super.state == failed);
            break;
    }
}

int _task_raise_error_if_set_result_forbidden(Task* self) {
    assert(self->coroutine != NULL);

    enum CoroState coro_state = get_coroutine_state(self->coroutine);
    if (coro_state == CORO_RUNNING) {
        PyErr_SetNone(_aio_self_cancel_forbidden_exc_cls);
        return -1;
    }
    if (coro_state != CORO_CLOSED && coro_state != CORO_CREATED) {
        PyErr_Format(
            PyExc_RuntimeError,
            "Attempt to finish task %R before it coroutine finished. "
            "Current coroutine state %s. Setting task result "
            "allowed either when coroutine finished normally, or if it not started.",
            self,
            coro_state_to_str(coro_state)

        );
        return -1;
    }
    return 0;
}

typedef PyObject* (*_future_set_delegate) (Future*, PyObject*, PyObject*);

PyObject* _task_result_delegate(Task* self, PyObject* result, _future_set_delegate delegate) {
    if (_task_raise_error_if_set_result_forbidden(self) == -1)
        return NULL;
    
    if (_task_validate_can_go_to_state(self, TASK_FUT_STATE) == -1)
        return NULL;

    PyObject* res = delegate((Future*) self, result, NULL);
    if (res == NULL)
        return NULL;

    _task_do_go_to_state(self, TASK_FUT_STATE, (_TaskData) { .fut = {} });
    return res;
}

PyObject* _task_set_result(Task* self, PyObject* result, PyObject* _) {
    return _task_result_delegate(self, result, future_set_result);
}

PyObject* _task_set_exception(Task* self, PyObject* exc, PyObject* _) {
    return _task_result_delegate(self, exc, future_set_exception);
}

int _task_set_cancellation(Task* self, PyObject* cancellation) {
    if (_task_validate_can_go_to_state(self, TASK_CANCELLING) == -1)
        return -1;

    PyObject* handle = PyObject_CallMethod(
        self->super.loop, "call_soon", "(OO)", self->step_fn_as_callback, Py_NewRef(Py_None)
    );
    if (handle == NULL)
        return -1;

    _task_do_go_to_state(
        self,
        TASK_CANCELLING,
        (_TaskData) { 
            .cancelling = {
                .handle = handle,
                .inner_cancellation = Py_NewRef(cancellation)
            }
        }
    );
    return 0;
}

PyObject* task_cancel(Task* self, PyObject* cancellation) {
    assert(self->coroutine);

    if (PyObject_IsInstance(cancellation, _aio_cancelled_exc_cls) != 1) {
        PyErr_SetString(PyExc_TypeError, "Cancellation exception must be `aio.Cancelled` subclass");
        return NULL;
    }

    if (get_coroutine_state(self->coroutine) == CORO_RUNNING) {
        PyErr_SetObject((PyObject*) Py_TYPE(cancellation), cancellation);
        return NULL;
    }

    int is_wo_finished;
    PyObject* res = NULL;
    switch (self->state) {
        case TASK_CREATED:
            if (_task_set_cancellation(self, cancellation) == -1)
                return NULL;

            return Py_NewRef(Py_None);
        case TASK_SCHEDULED:
            assert(self->data.scheduled.handle != NULL);

            res = PyObject_CallMethod(self->data.scheduled.handle, "cancel", "");
            Py_XDECREF(res);
            if (res == NULL)
                return NULL;

            if (_task_set_cancellation(self, cancellation) == -1)
                return NULL;

            return Py_NewRef(Py_None);
        case TASK_RUNNING_STATE:
            is_wo_finished = _aio_abc_future_is_finished(self->data.running.waiting_on);
            if (is_wo_finished == -1) {
                return NULL;
            } else if (is_wo_finished == 0) {
                if (_aio_future_cancel(self->data.running.waiting_on, cancellation) == -1)
                    return NULL;
            } else {
                if (_task_validate_can_go_to_state(self, TASK_CANCELLING) == -1)
                    return NULL;

                res = _aio_abc_future_call_method(
                    self->data.running.waiting_on,
                    "remove_callback",
                    self->step_fn_as_callback
                );
                Py_XDECREF(res);
                if (res == NULL)
                    return NULL;

                if (_task_set_cancellation(self, cancellation) == -1)
                    return NULL;
            }
            return Py_NewRef(Py_None);
        case TASK_CANCELLING:
            PyErr_SetNone(_aio_already_cancalling_exc_cls);
            return NULL;
        case TASK_FUT_STATE:
            return _task_set_exception(self, cancellation, NULL);
        default:
            _fut_raise_unexpected_state();
            return NULL;
    }
}

PyObject* task_schedule(Task* self, PyObject* _) {
    if (self->state != TASK_CREATED) {
        PyErr_SetString(PyExc_RuntimeError, "Only newly created tasks can be scheduled for first step");
        return NULL;
    }
    if (_task_validate_can_go_to_state(self, TASK_SCHEDULED) == -1)
        return NULL;

    PyObject* handle = PyObject_CallMethod(
        self->super.loop,
        "call_soon", "(OO)",
        self->step_fn_as_callback,
        Py_NewRef(Py_None)
    );
    Py_DECREF(Py_None);
    if (handle == NULL)
        return NULL;

    _task_do_go_to_state(self, TASK_SCHEDULED, (_TaskData) { .scheduled = { .handle = handle } });
    return Py_NewRef(Py_None);
}

int _gen_status_from_result(PyObject** result) {
    // Stolen from `_asynciomodule.c` implementation
    if (*result != NULL)
        return PYGEN_NEXT;
    if (_PyGen_FetchStopIterationValue(result) == 0)
        return PYGEN_RETURN;

    assert(PyErr_Occurred() != NULL);
    return PYGEN_ERROR;
}

PyObject* task_step(Task* self, PyObject* arg) {
    assert(self->context);
    assert(self->coroutine);
    assert(PyErr_Occurred() == NULL);

    if (self->state != TASK_SCHEDULED && self->state != TASK_RUNNING_STATE && self->state != TASK_CANCELLING) {
        PyErr_SetString(PyExc_RuntimeError, "Trying to resume finished task");
        return NULL;
    }

    int cv_res;
    int gen_status = PYGEN_ERROR;
    PyObject* result_or_future = NULL;
    
    cv_res = PyContext_Enter(self->context);
    if (cv_res == -1)
        return NULL;

    PyObject* token = PyContextVar_Set(_aio_current_task_cv, (PyObject*) self);
    if (token == NULL)
        return NULL;

    switch (self->state) {
        case TASK_SCHEDULED:
        case TASK_RUNNING_STATE:
            gen_status = PyIter_Send((PyObject*) self->coroutine, Py_None, &result_or_future);
            break;
        case TASK_CANCELLING:
            result_or_future = _PyObject_CallMethodIdOneArg(
                (PyObject*) self->coroutine, &PyId_throw, self->data.cancelling.inner_cancellation
            );
            gen_status = _gen_status_from_result(&result_or_future);
            break;
        default:
            assert(0);
    }

    cv_res = PyContextVar_Reset(_aio_current_task_cv, token);
    Py_DECREF(token);
    if (cv_res == -1)
        goto fail;
    cv_res = PyContext_Exit(self->context);
    if (cv_res == -1)
        goto fail;

    if (gen_status == PYGEN_NEXT) {
        assert(result_or_future != NULL);

        if (_task_validate_can_go_to_state(self, TASK_RUNNING_STATE) == -1)
            goto fail;

        if (_is_aio_abc_future(result_or_future) != 1) {
            PyErr_SetString(PyExc_RuntimeError, "All `aio` coroutines must yield and `Feature` instance");
            goto fail;
        }
        if ((PyObject*) result_or_future == (PyObject*) self) {
            PyErr_SetString(
                PyExc_RuntimeError,
                "Task awaiting on itself, this will cause infinity awaiting that's why is forbidden"
            );
            goto fail;
        }

        PyObject* loop = PyObject_GetAttrString(result_or_future, "loop");
        if (loop == NULL)
            goto fail;
        
        // A bit risky, but we only need loop address, not value, so in case of it deallocation, we would produce fatals
        Py_DECREF(loop);
        if (self->super.loop != loop) {
            PyErr_Format(
                PyExc_RuntimeError,
                "During processing task `%R` another "
                "feature has been `%R` received, which "
                "does not belong to the same loop",
                self,
                result_or_future
            );
            goto fail;
        }

        PyObject* res = PyObject_CallMethod(result_or_future, "add_callback", "(O)", self->step_fn_as_callback);
        Py_XDECREF(res);
        if (res == NULL)
            goto fail;

        _task_do_go_to_state(self, TASK_RUNNING_STATE, (_TaskData) { .running = { .waiting_on = result_or_future } });
        return Py_NewRef(Py_None);
    } else if (gen_status == PYGEN_RETURN) {
        if (_task_validate_can_go_to_state(self, TASK_FUT_STATE) == -1)
            goto fail;
        
        PyObject* res = _task_set_result(self, result_or_future, NULL);
        Py_DECREF(result_or_future);
        Py_XDECREF(res);
        if (res == NULL)
            goto fail;

        return Py_NewRef(Py_None);
    } else if (gen_status == PYGEN_ERROR) {
        assert(PyErr_Occurred() != NULL);
        assert(result_or_future == NULL);

        if (!PyErr_ExceptionMatches(PyExc_Exception) && !PyErr_ExceptionMatches(_aio_cancelled_exc_cls))
            goto fail;
        
        PyObject* exc_type;
        PyObject* exc_value;
        PyObject* tb;
        PyErr_Fetch(&exc_type, &exc_value, &tb);
        assert(exc_type);
        PyErr_NormalizeException(&exc_type, &exc_value, &tb);
        if (tb == NULL)
            PyException_SetTraceback(exc_value, tb);
        
        PyObject* res = _task_set_exception(self, exc_value, NULL);
        Py_XDECREF(res);
        Py_DECREF(exc_type);
        Py_XDECREF(exc_value);
        Py_XDECREF(tb);
        if (res == NULL)
            goto fail;

        return Py_NewRef(Py_None);
    } else {
        PyErr_BadInternalCall();
        goto fail;
    }

fail:
    Py_XDECREF(result_or_future);
    return NULL;
}

PyObject* task_method_forbidden(Task* self, PyObject* _) {
    PyErr_SetString(PyExc_RuntimeError, "This method is forbidden for task");
    return NULL;
}

PyGetSetDef task_getset[] = {
    {"state", (getter) task_state_getter, NULL, NULL, NULL},
    {NULL}
};

static PyMethodDef task_methods[] = {
    {
        "_schedule",
        (PyCFunction) task_schedule,
        METH_NOARGS,
        "Schedule task first step (method is private, don't use)"
    },
    {
        "_cancel",
        (PyCFunction) task_cancel,
        METH_O,
        "Cancel task (method is private, don't use)"
    },
    {
        "_set_result",
        (PyCFunction) task_method_forbidden,
        METH_O,
        "This method is forbidden for `aio.future.cimpl.Task` class"
    },
    {
        "_set_exception",
        (PyCFunction) task_method_forbidden,
        METH_O,
        "This method is forbidden for `aio.future.cimpl.Task` class"
    },
    {
        "_step",
        (PyCFunction) task_step,
        METH_O,
        "Perform coroutine step and set result on completion, could "
        "be invoked only when task in `scheduled` or `running` states",
    },
    {NULL}
};

static PyTypeObject TaskType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "aio.future.cimpl.Task",
    .tp_doc = PyDoc_STR("Task C implementation"),
    .tp_base = &FutureType,
    .tp_basicsize = sizeof(Task),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = task_new,
    .tp_init = (initproc) task_init,
    .tp_dealloc = (destructor) task_destroy,
    .tp_methods = task_methods,
    .tp_getset = task_getset,
    .tp_repr = (reprfunc) task_repr
};

/* MODULE INITIALIZATION */
void _cimpl_deinit(void*);

static PyModuleDef cfuture_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "aio.future.cimpl",
    .m_doc = "Future and Task C implementation",
    .m_size = -1,
    .m_free = _cimpl_deinit
};

#define _ADD_TYPE(m, name, type) \
    if (PyType_Ready(&type) < 0) { \
        Py_DECREF(m); \
        return NULL; \
    } \
    Py_INCREF(&type); \
    if (PyModule_AddObject(m, #name, (PyObject*) &type) < 0) { \
        Py_DECREF(&type); \
        Py_DECREF(m); \
        return NULL; \
    }


#define _AIO_IMPORT_CLS(target, module, name) \
    target = (PyObject*) _import_cls(module, name); \
    if (target == NULL) \
        return NULL;


PyMODINIT_FUNC
PyInit__cimpl(void) {
    _AIO_IMPORT_CLS(_aio_abc_event_loop_cls, "aio.interfaces", "EventLoop");
    _AIO_IMPORT_CLS(_aio_abc_future_cls, "aio.interfaces", "Future");
    _AIO_IMPORT_CLS(_aio_cancelled_exc_cls, "aio.exceptions", "Cancelled");
    _AIO_IMPORT_CLS(_aio_fut_not_ready_exc_cls, "aio.exceptions", "FutureNotReady");
    _AIO_IMPORT_CLS(_aio_fut_finished_exc_cls, "aio.exceptions", "FutureFinishedError");
    _AIO_IMPORT_CLS(_aio_self_cancel_forbidden_exc_cls, "aio.exceptions", "SelfCancelForbidden");
    _AIO_IMPORT_CLS(_aio_already_cancalling_exc_cls, "aio.exceptions", "AlreadyCancelling");
    
    _aio_future_states_enum = PyObject_GetAttrString(_aio_abc_future_cls, "State");
    if (_aio_future_states_enum == NULL)
        return NULL;
    
    _aio_current_task_cv = _import_object("aio.future._priv", "current_task_cv");
    if (_aio_current_task_cv == NULL)
        return NULL;

    PyObject* m = PyModule_Create(&cfuture_module);
    if (m == NULL)
        return NULL;

    _ADD_TYPE(m, Future, FutureType);
    _ADD_TYPE(m, FutureAwaitIter, FutureAwaitIterType);
    _ADD_TYPE(m, Task, TaskType);

    return m;
}

void _cimpl_deinit(void* _) {
    Py_XDECREF(_aio_abc_event_loop_cls);
    Py_XDECREF(_aio_abc_future_cls);
    Py_XDECREF(_aio_future_states_enum);
    Py_XDECREF(_aio_cancelled_exc_cls);
    Py_XDECREF(_aio_fut_not_ready_exc_cls);
    Py_XDECREF(_aio_fut_finished_exc_cls);
    Py_XDECREF(_aio_self_cancel_forbidden_exc_cls);
    Py_XDECREF(_aio_already_cancalling_exc_cls);
    Py_XDECREF(_aio_current_task_cv);
}
