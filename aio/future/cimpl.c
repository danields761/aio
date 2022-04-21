#include <Python.h>
#include "cimpl.h"
#include "structmember.h"


/* UTILS */
PyTypeObject* get_exception(const char* name) {
    PyObject* errors_module = PyImport_ImportModule("aio.exceptions");
    if (errors_module == NULL)
        return NULL;
    
    PyObject* exc = PyObject_GetAttrString(errors_module, name);
    Py_DECREF(errors_module);
    if (exc == NULL)
        return NULL;

    if (!PyObject_IsSubclass(exc, (PyObject*) PyExc_BaseException)) {
        Py_DECREF(exc);
        PyErr_Format(PyExc_TypeError, "Expect `%s` to be an `BaseException` subclass", name);
        return NULL;
    }

    return (PyTypeObject*) exc;
}

PyTypeObject* get_loop_cls() {
    PyObject* module = PyImport_ImportModule("aio.interfaces");
    if (module == NULL)
        return NULL;

    PyObject* cls = PyObject_GetAttrString(module, "EventLoop");
    Py_DECREF(module);
    return (PyTypeObject*) cls;
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

int future_init(Future* self, PyObject* args, PyObject* kwds) {
    PyTypeObject* loop_cls = get_loop_cls();
    if (loop_cls == NULL)
        return -1;
    
    printf("After import %p", PyErr_Occurred());

    PyObject* loop = NULL;
    PyObject* label = NULL;

    int parse_result = PyArg_ParseTuple(args, "O!U", loop_cls, &loop, &label);
    printf("After parse %p", PyErr_Occurred());
    Py_DECREF(loop_cls);
    if (!parse_result)
        return -1;

    if (loop) {
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

void _future_destroy_impl(Future* self) {
    PyObject* exc_type = NULL;
    PyObject* exc = NULL;
    PyObject* tb = NULL;
    
    // Raise warnings without active exception to protect from crush in exception
    if (PyErr_Occurred()) {
        PyErr_Fetch(&exc_type, &exc, &tb);
        PyErr_Clear();
    }
    if (self->state == failed && !self->data.failed.exc_retrieved) {
        PyObject* exc = self->data.failed.exc;
        PyErr_WarnFormat(
            PyExc_UserWarning,
            2,
            "Feature `%R` is about to be destroyed, but her exception %R was never " \
            "retrieved. Please, `await` this feature, or call `result` or " \
            "`exception` methods to prevent exception being ignored silently.",
            (PyObject*) self,
            exc
        );
        // TODO
        if (0 && !PyErr_Occurred()) {
            PyErr_SetObject(PyExceptionInstance_Class(exc), exc);
            PyErr_WriteUnraisable((PyObject*) self);
            PyErr_Clear();   
        }
    }
    if (self->state == pending) {
        PyErr_WarnFormat(
            PyExc_UserWarning,
            2,
            "Feature `%R` is about to be destroyed, but not finished, that normally should never occur.",
            (PyObject*) self
        );
    }
    if (exc_type) {
        PyErr_Restore(exc_type, exc, tb);
    }

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

PyObject* future_repr(Future* self) {
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
    }
    PyObject* res = PyUnicode_FromFormat("<Future label=\"%U\" state=%U>", label, state_name);
    if (clear_label)
        Py_DECREF(label);
    Py_DECREF(state_name);
    return res;
}

void _raise_unexpected_future_state(void) {
    PyErr_SetString(PyExc_RuntimeError, "Unexpected future state");
}

PyObject* future_state_getter(Future* self, void* closure) {
    PyObject* interfaces_module = PyImport_ImportModule("aio.interfaces");
    if (interfaces_module == NULL)
        return NULL;
    
    PyObject* future_cls = PyObject_GetAttrString(interfaces_module, "Future");
    Py_DECREF(interfaces_module);
    if (future_cls == NULL) {
        return NULL;
    }
    
    PyObject* state_enum = PyObject_GetAttrString(future_cls, "State");
    Py_DECREF(future_cls);
    if (state_enum == NULL)
        return NULL;

    PyObject* res = NULL;
    switch (self->state) {
        case pending:
            res = PyObject_GetAttrString(state_enum, "running");
            break;
        case success:
        case failed:
            res = PyObject_GetAttrString(state_enum, "finished");
            break;
        default:
            _raise_unexpected_future_state();
    }
    Py_DECREF(state_enum);
    return res;
}

void _raise_future_not_ready(void) {
    PyTypeObject* exc = get_exception("FutureNotReady");
    PyErr_SetString((PyObject*) exc, "Future not ready");
}

PyObject* future_result(Future* self, PyObject* _) {
    PyObject* exc;
    switch (self->state) {
        case pending:
            _raise_future_not_ready();
            return NULL;
        case success:
            return Py_NewRef(self->data.success.result);
        case failed:
            exc = self->data.failed.exc;
            PyErr_SetObject(PyExceptionInstance_Class(exc), exc);
            return NULL;
        default:
            _raise_unexpected_future_state();
    }
    return NULL;
}

PyObject* future_exception(Future* self, PyObject* _) {
    switch (self->state) {
        case pending:
            _raise_future_not_ready();
            break;
        case success:
            return Py_None;
        case failed:
            self->data.failed.exc_retrieved = 1;
            return Py_NewRef(self->data.failed.exc);
        default:
            _raise_unexpected_future_state();
    }
    return NULL;
}

PyObject* future_add_callback(Future* self, PyObject* cb, PyObject* _) {
    if (!PyCallable_Check(cb)) {
        PyErr_SetString(PyExc_TypeError, "Callback must be callable object");
        return NULL;
    }

    PyObject* exc = (PyObject*) get_exception("FutureFinishedError");
    if (exc == NULL)
        return NULL;

    switch (self->state) {
        case pending:
            if (PySet_Add(self->data.pending.result_callbacks, cb) == -1)
                return NULL;
            else
                return Py_None;
        case success:
        case failed:
            PyErr_SetString(exc, "Could not schedule callback for already finished future");
            return NULL;
        default:
            _raise_unexpected_future_state();
    }
    return NULL;
}

int _is_key_or_value_error(void) {
    return PyErr_Occurred() && (PyErr_ExceptionMatches(PyExc_ValueError) || PyErr_ExceptionMatches(PyExc_KeyError));
}

int _is_key_error(void) {
    return PyErr_Occurred() && PyErr_ExceptionMatches(PyExc_ValueError);
}

PyObject* future_remove_callback(Future* self, PyObject* cb, PyObject* _) {
    PyObject* sched_cbs;
    PyObject* exc = (PyObject*) get_exception("FutureFinishedError");
    if (exc == NULL)
        return NULL;

    switch (self->state) {
        case pending:
            if (PySet_Discard(self->data.pending.result_callbacks, cb) == -1) {
                if (!_is_key_or_value_error()) {
                    return NULL;
                }
                PyErr_Clear();
            }
            return Py_None;
        case success:
            sched_cbs = self->data.success.scheduled_cbs;
            break;
        case failed:
            sched_cbs = self->data.failed.scheduled_cbs;
            break;
        default:
            _raise_unexpected_future_state();
            return NULL;
    }

    PyObject* handle = PyDict_GetItemWithError(sched_cbs, cb);
    if (handle == NULL) {
        if (!_is_key_error()) {
            return NULL;
        } else {
            PyErr_Clear();
            return Py_None;
        }
    }
    if (PyDict_DelItem(sched_cbs, cb) == -1)
        return NULL;

    PyObject* cancel_res = PyObject_CallMethod(handle, "cancel", (const char*) NULL);
    if (cancel_res == NULL) {
        return NULL;
    } else {
        return Py_None;
    }
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

int _set_error_if_finished(Future* self) {
    if (self->state == pending) {
        return 0;
    }

    PyObject* exc = (PyObject*) get_exception("FutureFinishedError");
    if (exc == NULL) {
        return -1;
    }
    PyErr_SetString(exc, "Future already finished");
    return -1;
}

PyObject* _before_change_state(Future* self) {
    if (_set_error_if_finished(self) == -1) {
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
    if (scheduled == NULL) {
        return NULL;
    }

    Py_INCREF(res);
    self->state = success;
    self->data.success.result = res;
    self->data.success.scheduled_cbs = scheduled;
    return Py_None;
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
    
    Py_INCREF(exc);
    self->state = failed;
    self->data.failed.exc = exc;
    self->data.failed.exc_retrieved = 0;
    self->data.failed.scheduled_cbs = scheduled;

    return Py_None;
}

PyObject* future_is_finished_getter(Future* self, void* closure) {
    switch (self->state) {
        case pending:
            return Py_False;
        case success:
        case failed:
            return Py_True;
        default:
            _raise_unexpected_future_state();
    }
    return NULL;
}

PyObject* future_is_cancelled_getter(Future* self, void* closure) {
    PyTypeObject* cancelled_exc;
    switch (self->state) {
        case pending:
        case success:
            return Py_False;
        case failed:
            cancelled_exc = get_exception("Cancelled");
            if (PyObject_IsInstance(self->data.failed.exc, (PyObject*) cancelled_exc)) {
                return Py_True;
            } else {
                return Py_False;
            }
        default:
            _raise_unexpected_future_state();
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
    {"state", (getter) future_state_getter, NULL, NULL, NULL },
    {"is_finished", (getter) future_is_finished_getter, NULL, NULL, NULL },
    {"is_cancelled", (getter) future_is_cancelled_getter, NULL, NULL, NULL },
    {NULL}
};

static PyMethodDef future_methods[] = {
    {"result", (PyCFunction) future_result, METH_NOARGS, "Get future result (or raise `aio.FutureNotReady`)"},
    {"exception", (PyCFunction) future_exception, METH_NOARGS, "Get future exception (or raise `aio.FutureNotReady`)"},
    {
        "add_callback",
        (PyCFunction) future_add_callback,
        METH_O,
        "Get future exception (or raise `aio.FutureNotReady`)"
    },
    {
        "remove_callback",
        (PyCFunction) future_remove_callback,
        METH_O,
        "Get future exception (or raise `aio.FutureNotReady`)"
    },
    {
        "_set_result",
        (PyCFunction) future_set_result,
        METH_O,
        "Set future result and schedule pending callbacks (or raise `aio.FutureAlreadyFinished` if already set)"
    },
    {
        "_set_exception",
        (PyCFunction) future_set_exception,
        METH_O,
        "Set future exception and schedule pending callbacks (or raise `aio.FutureAlreadyFinished` if already set)"
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

    if (tb != NULL && !PyTraceBack_Check(tb)) {
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
    return Py_None;
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


/* MODULE INITIALIZATION */
static PyModuleDef cfuture_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "aio.future.cimpl",
    .m_doc = "Future and Task C implementation",
    .m_size = -1,
};

PyMODINIT_FUNC
PyInit__cimpl(void)
{
    PyObject *m;
    if (PyType_Ready(&FutureType) < 0)
        return NULL;
    if (PyType_Ready(&FutureAwaitIterType) < 0)
        return NULL;

    m = PyModule_Create(&cfuture_module);
    if (m == NULL)
        return NULL;

    Py_INCREF(&FutureType);
    if (PyModule_AddObject(m, "Future", (PyObject*) &FutureType) < 0) {
        Py_DECREF(&FutureType);
        Py_DECREF(m);
        return NULL;
    }

    Py_INCREF(&FutureAwaitIterType);
    if (PyModule_AddObject(m, "FutureAwaitIter", (PyObject*) &FutureAwaitIterType) < 0) {
        Py_DECREF(&FutureAwaitIterType);
        Py_DECREF(m);
        return NULL;
    }

    return m;
}
