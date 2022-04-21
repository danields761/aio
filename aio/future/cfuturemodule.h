#ifndef C_FUTURE_MODULE_H
#define C_FUTURE_MODULE_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

enum FutureState {
    pending,
    success,
    failed
};

typedef struct {
    PyObject_HEAD

    enum FutureState state;
    union {
        struct {
            PyObject* result_callbacks;
        } pending;
        struct {
            PyObject* result;
            PyObject* scheduled_cbs;
        } success;
        struct {
            PyObject* exc;
            int exc_retrieved;
            PyObject* scheduled_cbs;
        } failed;
    } data;
    PyObject* loop;
    PyObject* label;
} Future;

static PyTypeObject FutureType;

PyObject* future_new(PyTypeObject *type, PyObject *args, PyObject *kwds);
int future_init(Future* self, PyObject* args, PyObject* kwds);
void future_destroy(Future* self);
PyObject* future_repr(Future* self);
PyObject* future_state_getter(Future* self, void* closure);
PyObject* future_result(Future* self, PyObject* _);
PyObject* future_exception(Future* self, PyObject* _);
PyObject* future_add_callback(Future* self, PyObject* cb, PyObject* _);
PyObject* future_remove_callback(Future* self, PyObject* cb, PyObject* _);
PyObject* future_set_result(Future* self, PyObject* res, PyObject* _);
PyObject* future_set_exception(Future* self, PyObject* exc, PyObject* _);
PyObject* future_is_finished_getter(Future* self, void* closure);
PyObject* future_is_cancelled_getter(Future* self, void* closure);
PyObject* future_await(Future* self, PyObject* _);

// FutureAwaitIter class
typedef struct {
    PyObject_HEAD

    Future* fut;
    int first_send_occurred;
} FutureAwaitIter;

static PyTypeObject FutureAwaitIterType;

PyObject* future_await_iter_new(PyTypeObject *type, PyObject *args, PyObject *kwds);
int future_await_iter_init(FutureAwaitIter* self, PyObject* args, PyObject* kwds);
void future_await_iter_destroy(FutureAwaitIter* self);
PyObject* future_await_iter_next(FutureAwaitIter* self);
PyObject* future_await_iter_send(FutureAwaitIter* self, PyObject* value);
PyObject* future_await_iter_throw(FutureAwaitIter* self, PyObject* const* args, Py_ssize_t nargs);
PyObject* future_await_iter_close(FutureAwaitIter* self, PyObject* arg);


// Task class
typedef struct {
    PyObject_HEAD

    Future super;
};

#endif
