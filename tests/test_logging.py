import logging

import pytest

from tests.log_handler import PykkaTestLogHandler


pytestmark = pytest.mark.usefixtures('stop_all')


@pytest.fixture
def log_handler():
    log_handler = PykkaTestLogHandler()

    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)
    # pytest sets the root logger level to WARNING. We reset it to NOTSET
    # so that all log messages reaches our log handler.
    root_logger.setLevel(logging.NOTSET)

    yield log_handler

    log_handler.close()


@pytest.fixture(scope='module')
def actor_class(runtime):
    class ActorA(runtime.actor_class):
        def __init__(self, events):
            super(ActorA, self).__init__()
            self.events = events

        def on_stop(self):
            self.events.on_stop_was_called.set()

        def on_failure(self, exception_type, exception_value, traceback):
            self.events.on_failure_was_called.set()

        def on_receive(self, message):
            if message.get('command') == 'raise exception':
                return self.raise_exception()
            elif message.get('command') == 'raise base exception':
                raise BaseException()
            else:
                super(ActorA, self).on_receive(message)

        def raise_exception(self):
            raise Exception('foo')

    return ActorA


@pytest.fixture
def actor(actor_class, events):
    ref = actor_class.start(events)
    yield ref
    ref.stop()


def test_null_handler_is_added_to_avoid_warnings():
    logger = logging.getLogger('pykka')
    handler_names = [h.__class__.__name__ for h in logger.handlers]

    assert 'NullHandler' in handler_names


def test_unexpected_messages_are_logged(actor, log_handler):
    actor.ask({'unhandled': 'message'})

    log_handler.wait_for_message('warning')
    with log_handler.lock:
        assert len(log_handler.messages['warning']) == 1
        log_record = log_handler.messages['warning'][0]

    assert log_record.getMessage().split(': ')[0] == (
        'Unexpected message received by %s' % actor
    )


def test_exception_is_logged_when_returned_to_caller(actor, log_handler):
    with pytest.raises(Exception):
        actor.proxy().raise_exception().get()

    log_handler.wait_for_message('debug')
    with log_handler.lock:
        assert len(log_handler.messages['debug']) == 1
        log_record = log_handler.messages['debug'][0]

    assert log_record.getMessage() == (
        'Exception returned from %s to caller:' % actor
    )
    assert log_record.exc_info[0] == Exception
    assert str(log_record.exc_info[1]) == 'foo'


def test_exception_is_logged_when_not_reply_requested(
    actor, events, log_handler
):
    events.on_failure_was_called.clear()
    actor.tell({'command': 'raise exception'})

    events.on_failure_was_called.wait(5)
    assert events.on_failure_was_called.is_set()

    log_handler.wait_for_message('error')
    with log_handler.lock:
        assert len(log_handler.messages['error']) == 1
        log_record = log_handler.messages['error'][0]

    assert log_record.getMessage() == 'Unhandled exception in %s:' % actor
    assert log_record.exc_info[0] == Exception
    assert str(log_record.exc_info[1]) == 'foo'


def test_base_exception_is_logged(actor, events, log_handler):
    log_handler.reset()
    events.on_stop_was_called.clear()
    actor.tell({'command': 'raise base exception'})

    events.on_stop_was_called.wait(5)
    assert events.on_stop_was_called.is_set()

    log_handler.wait_for_message('debug', num_messages=3)
    with log_handler.lock:
        assert len(log_handler.messages['debug']) == 3
        log_record = log_handler.messages['debug'][0]

    assert log_record.getMessage() == (
        'BaseException() in %s. Stopping all actors.' % actor
    )


def test_exception_in_on_start_is_logged(
    early_failing_actor_class, events, log_handler
):
    log_handler.reset()
    actor = early_failing_actor_class.start(events)
    events.on_start_was_called.wait(5)

    log_handler.wait_for_message('error')
    with log_handler.lock:
        assert len(log_handler.messages['error']) == 1
        log_record = log_handler.messages['error'][0]

    assert log_record.getMessage() == 'Unhandled exception in %s:' % actor


def test_exception_in_on_stop_is_logged(
    late_failing_actor_class, events, log_handler
):
    log_handler.reset()
    actor = late_failing_actor_class.start(events)
    events.on_stop_was_called.wait(5)

    log_handler.wait_for_message('error')
    with log_handler.lock:
        assert len(log_handler.messages['error']) == 1
        log_record = log_handler.messages['error'][0]

    assert log_record.getMessage() == 'Unhandled exception in %s:' % actor


def test_exception_in_on_failure_is_logged(
    failing_on_failure_actor_class, events, log_handler
):
    log_handler.reset()
    actor = failing_on_failure_actor_class.start(events)
    actor.tell({'command': 'raise exception'})
    events.on_failure_was_called.wait(5)

    log_handler.wait_for_message('error', num_messages=2)
    with log_handler.lock:
        assert len(log_handler.messages['error']) == 2
        log_record = log_handler.messages['error'][0]

    assert log_record.getMessage() == 'Unhandled exception in %s:' % actor
