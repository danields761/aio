#ifndef CIMPL_MODULE_H
#define CIMPL_MODULE_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>


// Utils
enum CoroState {
    CORO_CREATED = 1 << 0,
    CORO_RUNNING = 1 << 1,
    CORO_SUSPENDED = 1 << 2,
    CORO_CLOSED = 1 << 3
};

enum CoroState get_coroutine_state(PyCoroObject* coroutine);


// Future class
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
enum TaskState {
    TASK_FUT_STATE,
    TASK_CREATED,
    TASK_SCHEDULED,
    TASK_CANCELLING,
    TASK_RUNNING_STATE
};

typedef struct {
} _TaskCreatedData;

typedef struct {
    PyObject* handle;
} _TaskScheduledData;

typedef struct {
    PyObject* waiting_on;
} _TaskRunningData;

typedef struct {
    PyObject* handle;
    PyObject* inner_cancellation;
} _TaskCancelling;

typedef struct {
} _TaskFutData;

typedef union {
    _TaskCreatedData created;
    _TaskScheduledData scheduled;
    _TaskRunningData running;
    _TaskCancelling cancelling;
    _TaskFutData fut;
} _TaskData;

typedef struct {
    Future super;

    enum TaskState state;
    _TaskData data;

    PyObject* context;

    PyCoroObject* coroutine;
    PyObject* step_fn_as_callback;
} Task;

static PyTypeObject TaskType;

PyObject* task_new(PyTypeObject *type, PyObject *args, PyObject *kwds);
int task_init(Task* self, PyObject* args, PyObject* kwds);
void task_destroy(Task* self);
PyObject* task_repr(Task* self);
PyObject* task_state_getter(Task* self, void* closure);
PyObject* task_schdule(Task* self, PyObject* _);
PyObject* task_cancel(Task* self, PyObject* cancellation);

#endif
