import json
from types import SimpleNamespace
import pytest
from pixcut.probe import Frames, PREFIX, QUERIES, query, probe_device


class UsbTimeout(Exception):
    pass


class FakeDevice:
    def __init__(self):
        self.writes = []
        self.incoming = []
        self.active_driver = False

    def get_active_configuration(self):
        return {
            (2, 0): [
                SimpleNamespace(bEndpointAddress=a, bmAttributes=2) for a in (6, 134)
            ]
        }

    def is_kernel_driver_active(self, interface):
        assert interface == 2
        return self.active_driver

    def write(self, endpoint, data, timeout):
        assert endpoint == 6
        message = json.loads(data[len(PREFIX) :])
        assert message["method"] == "get-prop"
        assert tuple(message["params"]) in [p for _, p in QUERIES]
        self.writes.append(message)
        reply = (
            PREFIX + json.dumps({"id": message["id"], "result": ["fixture"]}).encode()
        )
        self.incoming += [reply[:5], reply[5:]]
        return len(data)

    def read(self, endpoint, length, timeout):
        assert endpoint == 134
        return self.incoming.pop(0)


def utilities():
    calls = []
    return calls, SimpleNamespace(
        claim_interface=lambda d, i: calls.append(("claim", i)),
        release_interface=lambda d, i: calls.append(("release", i)),
        dispose_resources=lambda d: calls.append(("dispose",)),
    )


def test_only_read_queries_and_command_interface():
    device = FakeDevice()
    calls, util = utilities()
    result = probe_device(device, SimpleNamespace(USBTimeoutError=UsbTimeout), util)
    assert list(result) == ["identity", "media", "status"]
    assert len(device.writes) == 3
    assert calls == [("claim", 2), ("release", 2), ("dispose",)]


def test_driver_is_never_detached():
    device = FakeDevice()
    device.active_driver = True
    calls, util = utilities()
    with pytest.raises(RuntimeError, match="will not detach"):
        probe_device(device, SimpleNamespace(USBTimeoutError=UsbTimeout), util)
    assert device.writes == [] and calls == [("dispose",)]


def test_fragmented_utf8_and_coalesced_messages():
    frames = Frames()
    payload = (
        PREFIX
        + json.dumps({"id": 1, "result": "é"}, ensure_ascii=False).encode()
        + b"\0"
        + PREFIX
        + b'{"id":2,"result":[]}'
    )
    results = []
    for byte in payload:
        results.extend(frames.feed(bytes([byte])))
    assert results == [{"id": 1, "result": "é"}, {"id": 2, "result": []}]
    with pytest.raises(ValueError, match="64 KiB"):
        Frames().feed(b"x" * 65537)


def test_uncorrelated_events_are_not_success():
    device = FakeDevice()
    device.incoming = [b'{"method":"event.changed"}{"id":3,"result":[]}']
    result = query(device, QUERIES[0][1], 5, UsbTimeout)
    assert result["response"]["id"] == 5
    assert len(result["other_messages"]) == 2
    with pytest.raises(ValueError, match="fixed read-only"):
        query(device, ("set-prop",), 6, UsbTimeout)


def test_timeout_is_not_retried_and_claim_is_released():
    device = FakeDevice()
    device.read = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("disconnected")
    )
    calls, util = utilities()
    with pytest.raises(RuntimeError, match="disconnected"):
        probe_device(device, SimpleNamespace(USBTimeoutError=UsbTimeout), util)
    assert len(device.writes) == 1
    assert calls == [("claim", 2), ("release", 2), ("dispose",)]
    with pytest.raises(TimeoutError, match="no retry"):
        query(FakeDevice(), QUERIES[0][1], 1, UsbTimeout, timeout_s=0)
