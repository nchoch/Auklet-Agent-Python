from __future__ import absolute_import, division, unicode_literals

import sys

from time import time
import pprint
import inspect
from uuid import uuid4
from auklet.protobuf import data_pb2

try:
    import psutil
except ImportError:
    # Some platforms that applications could be running on require specific
    # installation of psutil which we cannot configure currently
    psutil = None

__all__ = ['MonitoringTree', 'Event', 'SystemMetrics']


class Function(object):
    samples = 1
    calls = 0
    line_num = ''
    func_name = ''
    file_path = ''

    children = []
    parent = None

    def __init__(self, line_num, func_name, file_path=None,
                 parent=None, calls=0):
        self.line_num = line_num
        self.func_name = func_name
        self.parent = parent
        self.children = []
        self.file_path = file_path
        self.calls = calls

    def __str__(self):
        pp = pprint.PrettyPrinter()
        return pp.pformat(dict(self))

    def __iter__(self):
        yield "functionName", self.func_name
        yield "nSamples", self.samples
        yield "lineNumber", self.line_num
        yield "nCalls", self.calls
        yield "filePath", self.file_path
        yield "callees", [dict(item) for item in self.children]

    def has_child(self, test_child):
        print(self.children)
        for child in self.children:
            if test_child.func_name == child.func_name \
                    and test_child.file_path == child.file_path:
                return child
        return False


class Event(object):
    trace = []
    exc_type = None
    line_num = 0
    abs_path = None
    filters = ["auklet"]

    def __init__(self, exc_type, tb, tree, abs_path):
        self.exc_type = exc_type.__name__
        self.line_num = tb.tb_lineno
        self.abs_path = abs_path
        self._build_traceback(tb, tree)

    def __iter__(self):
        yield "stackTrace", self.trace
        yield "excType", self.exc_type

    def _filter_frame(self, file_name):
        if any(filter_str in file_name for filter_str in self.filters) or \
                file_name is None:
            return True
        return False

    def _convert_locals_to_string(self, local_vars):
        for key in local_vars:
            if type(local_vars[key]) != str and type(local_vars[key]) != int:
                local_vars[key] = str(local_vars[key])
        return local_vars

    def _build_traceback(self, trace, tree):
        tb = []
        while trace:
            frame = trace.tb_frame
            path = tree.get_filename(frame.f_code, frame)
            if self._filter_frame(path):
                trace = trace.tb_next
                continue
            if self.abs_path in path:
                path = path.replace(self.abs_path, '')
            tb.append({"functionName": frame.f_code.co_name,
                       "filePath": path,
                       "lineNumber": frame.f_lineno,
                       "locals":
                           self._convert_locals_to_string(frame.f_locals)})
            trace = trace.tb_next
        self.trace = tb


class MonitoringTree(object):
    commit_hash = None
    root_func = None
    public_ip = None
    mac_hash = None
    abs_path = None
    cached_filenames = {}
    filters = ["site-packages", "Python.framework", "auklet", "lib/python",
               "importlib"]

    def __init__(self, mac_hash=None):
        from auklet.base import get_device_ip, get_commit_hash, get_abs_path
        self.commit_hash = get_commit_hash()
        self.public_ip = get_device_ip()
        self.abs_path = get_abs_path('.auklet/version')
        self.mac_hash = mac_hash

    def get_filename(self, code, frame):
        key = code.co_code
        file_name = self.cached_filenames.get(code.co_code, None)
        print(file_name)
        if file_name is None:
            try:
                file_name = inspect.getsourcefile(frame) or \
                            inspect.getfile(frame)
            except (TypeError, AttributeError):
                # These functions will fail if the frame is of a
                # built-in module, class or function
                return None
            self.cached_filenames[key] = file_name
        return file_name

    def _create_frame_func(self, frame, root=False, parent=None):
        if root:
            return Function(
                line_num=1,
                func_name="root",
                parent=None,
                file_path=None,
                calls=1
            )

        calls = 0
        if frame[1]:
            calls = 1
        frame = frame[0]

        file_path = self.get_filename(frame.f_code, frame)
        if file_path is not None:
            if self.abs_path in file_path:
                file_path = file_path.replace(self.abs_path, '')
        return Function(
            line_num=frame.f_code.co_firstlineno,
            func_name=frame.f_code.co_name,
            parent=parent,
            file_path=file_path,
            calls=calls
        )

    def _filter_frame(self, file_name):
        if file_name is None or \
                any(filter_str in file_name for filter_str in self.filters):
            return True
        return False

    def _build_tree(self, new_stack):
        root_func = self._create_frame_func(None, True)
        parent_func = root_func
        for frame in reversed(new_stack):
            current_func = self._create_frame_func(
                frame, parent=parent_func)
            if self._filter_frame(current_func.file_path):
                continue
            parent_func.children.append(current_func)
            parent_func = current_func
        return root_func

    def _build_protobuf_tree(self, root_tree, message):
        try:
            if isinstance(root_tree, (list,)):
                tree = root_tree[0]
            elif isinstance(root_tree, (dict,)):
                tree = root_tree

            message.callees.add().functionName = tree['functionName']
            message.callees.add().nSamples = tree['nSamples']
            message.callees.add().lineNumber = tree['lineNumber']
            message.callees.add().nCalls = tree['nCalls']
            message.callees.add().filePath = str(tree['filePath'])

            if isinstance(root_tree, (list,)):
                if len(root_tree) == 1:
                    self._build_protobuf_tree(root_tree[0]['callees'], message)
                else:
                    for i in range(1, len(root_tree)):
                        self._build_protobuf_tree(root_tree[i], message)

            if isinstance(root_tree, (dict,)):
                self._build_protobuf_tree(root_tree['callees'], message)

        except IndexError:
            pass
        finally:
            return message.SerializeToString()

    def build_protobuf_monitoring_data(self, app_id):
        self.protobuf_monitoring_data = data_pb2.ProtobufMonitoringData()

        self.protobuf_monitoring_data.application = str(app_id)
        self.protobuf_monitoring_data.publicIP = str(self.public_ip)
        self.protobuf_monitoring_data.id = str(uuid4())
        self.protobuf_monitoring_data.timestamp = int(round(time()) * 1000)
        self.protobuf_monitoring_data.systemMacHash = str(self.mac_hash)
        self.protobuf_monitoring_data.commitHash = str(self.commit_hash)

        root_tree = dict(self.root_func)

        return self._build_protobuf_tree(root_tree, self.protobuf_monitoring_data)

    def _update_sample_count(self, parent, new_parent):
        if not new_parent.children:
            return True
        new_child = new_parent.children[0]
        has_child = parent.has_child(new_child)
        if has_child:
            has_child.calls += new_child.calls
            has_child.samples += 1
            return self._update_sample_count(has_child, new_child)
        parent.children.append(new_child)

    def update_hash(self, new_stack):
        new_tree_root = self._build_tree(new_stack)
        if self.root_func is None:
            self.root_func = new_tree_root
            return self.root_func
        self.root_func.samples += 1
        self._update_sample_count(self.root_func, new_tree_root)

    def clear_root(self):
        self.root_func = None
        return True

    def build_tree(self, app_id):
        return {
            "application": app_id,
            "publicIP": self.public_ip,
            "id": str(uuid4()),
            "timestamp": int(round(time() * 1000)),
            "macAddressHash": self.mac_hash,
            "commitHash": self.commit_hash,
            "tree": dict(self.root_func)
        }


class SystemMetrics(object):
    cpu_usage = 0.0
    mem_usage = 0.0
    inbound_network = 0
    outbound_network = 0
    prev_inbound = 0
    prev_outbound = 0

    def __init__(self):
        if psutil is not None:
            self.cpu_usage = psutil.cpu_percent(interval=1)
            self.mem_usage = psutil.virtual_memory().percent
            network = psutil.net_io_counters()
            self.prev_inbound = network.bytes_recv
            self.prev_outbound = network.bytes_sent

    def __iter__(self):
        yield "cpuUsage", self.cpu_usage
        yield "memoryUsage", self.mem_usage
        yield "inboundNetwork", self.inbound_network
        yield "outboundNetwork", self.outbound_network

    def update_network(self, interval):
        if psutil is not None:
            network = psutil.net_io_counters()
            self.inbound_network = (network.bytes_recv -
                                    self.prev_inbound) / interval
            self.outbound_network = (network.bytes_sent -
                                     self.prev_outbound) / interval
            self.prev_inbound = network.bytes_recv
            self.prev_outbound = network.bytes_sent
