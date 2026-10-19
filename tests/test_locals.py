from __future__ import annotations

import io
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import scholaros.locals as locals_module
from scholaros.locals import MAX_LOG_BYTES, MAX_METRIC_BYTES, LocalRunService, _append_line


def make_service(tmp_path):
    return LocalRunService(tmp_path / "runs")


def test_create_and_detect(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print('ok')"), ("metrics.csv", b"step,acc,loss\n0,0.5,2.0\n")], note="smoke")
    assert run["status"] == "created"
    detected = service.detect_config(run["id"])
    assert detected["command"] == "python main.py"
    assert detected["csv_path"] == "metrics.csv"
    assert "acc" in detected["metric_columns"]


def test_create_rejects_bad_paths_and_metrics(tmp_path):
    service = make_service(tmp_path)
    for path in ("../x.py", ".env", "/abs.py", "a\\b.py"):
        try:
            service.create(entries=[(path, b"print(1)")], note="")
        except ValueError:
            pass
        else:
            raise AssertionError(f"路径不应被接受：{path}")
    run = service.create(entries=[("a.py", b"print(1)")], note="")
    try:
        service.validate_config(run["id"], command="python a.py", csv_path="m.csv", metric_columns=["1bad"])
    except ValueError:
        pass
    else:
        raise AssertionError("非法指标列名不应被接受")


def test_metrics_extraction_from_csv(tmp_path):
    service = make_service(tmp_path)
    run = service.create(
        entries=[("metrics.csv", b"step,accuracy,loss\n0,0.5,2.0\n1,0.8,1.0\n2,0.9,0.5\n")],
        note="metrics",
    )
    service.validate_config(run["id"], command="python main.py", csv_path="metrics.csv", metric_columns=["accuracy", "loss"])
    directory = service._run_dir(run["id"])
    record = service._read_record(directory)
    record["csv_path"] = "metrics.csv"
    record["metric_columns"] = ["accuracy", "loss"]
    service._write_record(directory, record)
    metrics = service._extract_metrics(directory)
    assert metrics["available"] is True
    assert metrics["rows"] == 3
    by_name = {item["column"]: item for item in metrics["summary"]}
    assert by_name["accuracy"]["max"] == 0.9
    assert by_name["loss"]["last"] == 0.5


def test_metrics_extraction_from_txt(tmp_path):
    service = make_service(tmp_path)
    run = service.create(
        entries=[("results.txt", b"# result\naccuracy: 0.91\nloss = 0.12\n")],
        note="txt",
    )
    config = service.validate_config(run["id"], command="python main.py", csv_path="results.txt", metric_columns=["accuracy", "loss"])
    assert config["csv_path"] == "results.txt"
    directory = service._run_dir(run["id"])
    record = service._read_record(directory)
    record["csv_path"] = "results.txt"
    record["metric_columns"] = ["accuracy", "loss"]
    service._write_record(directory, record)
    metrics = service._extract_metrics(directory)
    assert metrics["available"] is True
    assert metrics["rows"] == 1
    by_name = {item["column"]: item for item in metrics["summary"]}
    assert by_name["accuracy"]["last"] == 0.91
    assert by_name["loss"]["last"] == 0.12


def test_metric_path_must_be_csv_or_txt(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print(1)")], note="")
    try:
        service.validate_config(run["id"], command="python main.py", csv_path="metrics.json", metric_columns=[])
    except ValueError as exc:
        assert "CSV 或 TXT" in str(exc)
    else:
        raise AssertionError("指标文件扩展名不应被接受")


def test_run_writes_output_and_metrics_using_fake_process(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run = service.create(
        entries=[("metrics.csv", b"name,score\n",), ("main.py", b"print('hi')")],
        note="ech",
    )

    class FakeStream(io.StringIO):
        def close(self) -> None:
            pass

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStream("line one\nline two\n")
            self.returncode = 0
            self.pid = 1234

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    service.validate_config(run["id"], command="python main.py", csv_path="metrics.csv", metric_columns=["score"])
    service.start(run["id"], command="python main.py", csv_path="metrics.csv", metric_columns=["score"], acknowledge_risk=True)
    deadline = time.time() + 1
    while time.time() < deadline:
        detail = service.get(run["id"])
        if detail["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert detail["status"] == "completed"
    assert "line one" in detail["output"]
    assert detail["log_size"] > 0
    assert detail["exit_code"] == 0
    assert detail["metrics"]["available"] is True


def test_delete_removes_record(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print(1)")], note="del")
    assert (service.root / run["id"]).is_dir()
    service.delete(run["id"])
    assert not (service.root / run["id"]).exists()
    assert service.list() == []


def test_delete_running_record_stops_and_removes(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print('hi')")], note="run-del")

    class FakeStream(io.StringIO):
        def close(self) -> None:
            pass

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStream("output\n")
            self.returncode = 0
            self.pid = 999999

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(service, "_terminate", lambda process: None)
    service.start(run["id"], command="python main.py", csv_path="metrics.csv", metric_columns=[], acknowledge_risk=True)
    service.delete(run["id"])
    assert not (service.root / run["id"]).exists()
    assert service.list() == []


def test_delete_rejects_missing_or_invalid(tmp_path):
    service = make_service(tmp_path)
    try:
        service.delete("0" * 32)
    except ValueError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("不存在的记录不应被删除")
    try:
        service.delete("bad-id")
    except ValueError as exc:
        assert "格式无效" in str(exc)
    else:
        raise AssertionError("非法 ID 不应被接受")


def test_start_requires_risk_confirmation(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print('x')")], note="risk")
    try:
        service.start(run["id"], command="python main.py", csv_path="metrics.csv", metric_columns=[], acknowledge_risk=False)
    except ValueError as exc:
        assert "宿主机" in str(exc)
    else:
        raise AssertionError("未确认宿主机风险不应启动")


def test_preview_only_uploaded_regular_files(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("pkg/main.py", b"print('ok')")], note="code")
    run_id = run["id"]
    assert service.get(run_id)["files"] == ["pkg/main.py"]
    assert service.preview(run_id, "pkg/main.py")["content"] == "print('ok')"
    for name in ("../secret.txt", "pkg/not_uploaded.py"):
        try:
            service.preview(run_id, name)
        except ValueError:
            pass
        else:
            raise AssertionError("只能预览已上传目录的普通文件")
    outside = tmp_path / "secret.py"
    outside.write_text("secret")
    file = service.root / run_id / "workspace" / "pkg" / "main.py"
    file.unlink()
    file.symlink_to(outside)
    try:
        service.preview(run_id, "pkg/main.py")
    except ValueError as exc:
        assert "符号链接" in str(exc)
    else:
        raise AssertionError("不应预览目录外的符号链接")


def test_metric_symlink_and_size_limit(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("metrics.csv", b"accuracy\n1\n")], note="metrics")
    directory = service.root / run["id"]
    record = service._read_record(directory)
    record["csv_path"] = "metrics.csv"
    service._write_record(directory, record)
    path = directory / "workspace" / "metrics.csv"
    path.write_bytes(b"accuracy\n" + b"1\n" * MAX_METRIC_BYTES)
    assert "超过" in service._extract_metrics(directory)["error"]
    outside = tmp_path / "private.csv"
    outside.write_text("accuracy\n9\n")
    path.unlink()
    path.symlink_to(outside)
    assert service._extract_metrics(directory)["available"] is False


def test_log_line_stays_within_size_limit(tmp_path):
    path = tmp_path / "console.log"
    with path.open("w", encoding="utf-8") as handle:
        _append_line(handle, "中" * (MAX_LOG_BYTES // 2), path)
        _append_line(handle, "overflow", path)
    assert path.stat().st_size <= MAX_LOG_BYTES


def test_non_finite_metrics_do_not_break_json(tmp_path):
    import json

    service = make_service(tmp_path)
    run = service.create(entries=[("metrics.csv", b"loss\nNaN\ninf\n0.25\n")], note="values")
    directory = service.root / run["id"]
    record = service._read_record(directory)
    record["csv_path"] = "metrics.csv"
    service._write_record(directory, record)
    result = service.get(run["id"])["metrics"]
    assert result["summary"][0]["mean"] == 0.25
    json.dumps(result, allow_nan=False)


def test_concurrent_start_does_not_launch_twice(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print(1)")], note="concurrent")
    entered, release = threading.Event(), threading.Event()
    launches = []

    class FakeStream(io.StringIO):
        def close(self) -> None:
            pass

    class FakePopen:
        def __init__(self, *args, **kwargs):
            launches.append(1)
            entered.set()
            release.wait(3)
            self.stdout = FakeStream("")
            self.pid = 999999

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    options = dict(command="python main.py", csv_path="metrics.csv", metric_columns=[], acknowledge_risk=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.start, run["id"], **options)
        assert entered.wait(2)
        second = executor.submit(service.start, run["id"], **options)
        release.set()
        first.result()
        try:
            second.result()
        except RuntimeError:
            pass
        else:
            raise AssertionError("同一记录不能并发启动两次")
    assert len(launches) == 1


def test_create_accepts_many_files_and_large_single_file(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[(f"pkg/file_{index}.py", b"x") for index in range(151)]
                         + [("weights.bin", b"x" * (20 * 1024 * 1024 + 1))], note="many")
    assert len(run["file_hashes"]) == 152
    try:
        service.create(entries=[("a.py", b"a"), ("A.py", b"b")], note="")
    except ValueError:
        pass
    else:
        raise AssertionError("大小写冲突仍需被拒绝")


def test_create_streamed_rejects_total_over_limit(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    monkeypatch.setattr(locals_module, "MAX_WORKSPACE", 3)
    try:
        service.create_streamed(entries=[("a.py", io.BytesIO(b"abcd"))], note="too big")
    except ValueError:
        pass
    else:
        raise AssertionError("总量超限仍需被拒绝")
    assert service.list() == []


def test_start_missing_run_does_not_leave_empty_directory(tmp_path):
    service = make_service(tmp_path)
    run_id = "a" * 32
    try:
        service.start(run_id, command="python main.py", csv_path="metrics.csv", metric_columns=[], acknowledge_risk=True)
    except ValueError:
        pass
    else:
        raise AssertionError("不存在的记录不应启动")
    assert not (service.root / run_id).exists()


def test_symlink_run_directory_is_rejected(tmp_path):
    service = make_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run.json").write_text("{}")
    run_id = "b" * 32
    (service.root / run_id).symlink_to(outside, target_is_directory=True)
    try:
        service.get(run_id)
    except ValueError:
        pass
    else:
        raise AssertionError("记录目录本身的符号链接不应被读取")


def test_real_local_run_collects_stdout_stderr_and_csv(tmp_path):
    service = make_service(tmp_path)
    source = ("from pathlib import Path\n"
              "import sys\n"
              "print('hello stdout', flush=True)\n"
              "print('hello stderr', file=sys.stderr, flush=True)\n"
              "Path('results.csv').write_text('step,accuracy\\n1,0.9\\n')\n")
    run = service.create(entries=[("main.py", source.encode())], note="real")
    command = f"{shlex.quote(sys.executable)} -u main.py"
    service.start(run["id"], command=command, csv_path="results.csv", metric_columns=["accuracy"], acknowledge_risk=True)
    deadline = time.time() + 5
    while time.time() < deadline:
        detail = service.get(run["id"])
        if detail["status"] != "running":
            break
        time.sleep(0.02)
    assert detail["status"] == "completed", detail.get("output")
    assert "hello stdout" in detail["output"]
    assert "hello stderr" in detail["output"]
    assert detail["metrics"]["summary"][0]["last"] == 0.9


def test_stop_waits_for_completed_run_status(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print(1)")], note="stop-race")
    entered, release = threading.Event(), threading.Event()
    original = service._read_record

    def delayed_read(directory):
        if threading.current_thread().name.startswith("Thread-"):
            entered.set()
            release.wait(3)
        return original(directory)

    class FakeStream(io.StringIO):
        def close(self) -> None:
            pass

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStream("")
            self.pid = 999999

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(service, "_read_record", delayed_read)
    monkeypatch.setattr(service, "_terminate", lambda process: None)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    service.start(run["id"], command="python main.py", csv_path="metrics.csv", metric_columns=[], acknowledge_risk=True)
    assert entered.wait(2)
    with ThreadPoolExecutor(max_workers=1) as executor:
        stopped = executor.submit(service.stop, run["id"])
        release.set()
        assert stopped.result(timeout=5)["status"] == "completed"


def test_metrics_can_read_output_next_to_uploaded_src(tmp_path):
    service = make_service(tmp_path)
    run = service.create(entries=[("main.py", b"print('ok')")], note="external output")
    directory = service.root / run["id"]
    metric = directory / "outputs" / "trial" / "diabetes" / "metrics.csv"
    metric.parent.mkdir(parents=True)
    metric.write_text("fold,accuracy\n1,0.9\n2,0.8\n", encoding="utf-8")
    csv_path = "../outputs/trial/diabetes/metrics.csv"
    config = service.validate_config(run["id"], command="python main.py", csv_path=csv_path,
                                     metric_columns=["accuracy"])
    record = service._read_record(directory)
    record.update(config)
    service._write_record(directory, record)
    result = service._extract_metrics(directory)
    assert result["summary"][0]["count"] == 2
    assert result["summary"][0]["last"] == 0.8
    for invalid in ("../datasets/private.csv", "../outputs/../../secret.csv"):
        try:
            service.validate_config(run["id"], command="python main.py", csv_path=invalid, metric_columns=[])
        except ValueError:
            pass
        else:
            raise AssertionError("不应允许读取运行目录外的文件")
    metric.unlink()
    metric.symlink_to(tmp_path / "private.csv")
    assert service._extract_metrics(directory)["available"] is False
