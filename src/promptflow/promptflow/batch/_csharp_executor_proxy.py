# ---------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# ---------------------------------------------------------
import json
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from promptflow._core._errors import MetaFileNotFound, MetaFileReadError
from promptflow._sdk._constants import DEFAULT_ENCODING, FLOW_TOOLS_JSON, PROMPT_FLOW_DIR_NAME
from promptflow.batch._base_executor_proxy import APIBasedExecutorProxy
from promptflow.executor._result import AggregationResult
from promptflow.storage._run_storage import AbstractRunStorage

EXECUTOR_SERVICE_DOMAIN = "http://localhost:"
EXECUTOR_SERVICE_DLL = "Promptflow.dll"


class CSharpExecutorProxy(APIBasedExecutorProxy):
    def __init__(
        self, *, process: subprocess.Popen, port: str, working_dir: Path, temp_dag_file: Optional[Path] = None
    ):
        self._process = process
        self._port = port
        self._working_dir = working_dir
        self._temp_dag_file = temp_dag_file

    @property
    def api_endpoint(self) -> str:
        return EXECUTOR_SERVICE_DOMAIN + self._port

    def _get_flow_meta(self) -> dict:
        # TODO: this should be got from flow.json for all languages by default?
        flow_meta_json_path = self._working_dir / ".promptflow" / "flow.json"
        if not flow_meta_json_path.is_file():
            raise MetaFileNotFound(
                message_format=(
                    # TODO: pf flow validate should be able to generate flow.json
                    "Failed to fetch meta of inputs: cannot find {file_path}, please retry."
                ),
                file_path=flow_meta_json_path.absolute().as_posix(),
            )

        with open(flow_meta_json_path, mode="r", encoding=DEFAULT_ENCODING) as flow_meta_json_path:
            return json.load(flow_meta_json_path)

    def get_inputs_definition(self):
        """Get the inputs definition of an eager flow"""
        if self._temp_dag_file is None:
            raise MetaFileReadError(
                message_format="Should not call get_inputs_definition() for non-eager mode csharp flow.",
            )

        try:
            return super().get_inputs_definition()
        except MetaFileNotFound:
            # TODO: wait for csharp executor to support flow meta generation
            from promptflow.contracts.flow import FlowInputDefinition
            from promptflow.contracts.tool import ValueType

            return {
                "question": FlowInputDefinition(
                    type=ValueType.STRING,
                ),
            }

    @classmethod
    async def create(
        cls,
        flow_file: Path,
        working_dir: Optional[Path] = None,
        *,
        connections: Optional[dict] = None,
        storage: Optional[AbstractRunStorage] = None,
        **kwargs,
    ) -> "CSharpExecutorProxy":
        """Create a new executor"""
        port = cls.find_available_port()
        log_path = kwargs.get("log_path", "")
        init_error_file = Path(working_dir) / f"init_error_{str(uuid.uuid4())}.json"
        init_error_file.touch()

        assembly_folder = flow_file.parent
        temp_dag_file = None
        # TODO: should we change the interface to init the proxy (always pass entry for eager mode)?
        if "entry" in kwargs:
            # DO NOT change this path as current flow meta json path (in get_inputs_definition)
            # is generated based on this path
            temp_dag_file = Path(working_dir) / ".promptflow" / f"flow.dag.{str(uuid.uuid4())}.yaml"
            temp_dag_file.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_dag_file, "w") as f:
                yaml.dump({"entry": kwargs["entry"], "path": flow_file.as_posix()}, f)
            flow_file = temp_dag_file

            # generate flow meta
            subprocess.check_call(
                [
                    "dotnet",
                    EXECUTOR_SERVICE_DLL,
                    "--flow_meta",
                    "--yaml_path",
                    flow_file.as_posix(),
                    "--assembly_folder",
                    assembly_folder.absolute().as_posix(),
                ]
            )

        command = [
            "dotnet",
            EXECUTOR_SERVICE_DLL,
            "-e",
            "-p",
            port,
            "--yaml_path",
            flow_file.as_posix(),
            "--assembly_folder",
            assembly_folder.absolute().as_posix(),
            "--log_path",
            log_path,
            "--log_level",
            "Warning",
            "--error_file_path",
            init_error_file,
        ]
        process = subprocess.Popen(command)
        executor_proxy = cls(
            process=process,
            port=port,
            temp_dag_file=temp_dag_file,
            working_dir=working_dir,
        )
        try:
            await executor_proxy.ensure_executor_startup(init_error_file)
        finally:
            Path(init_error_file).unlink()
        return executor_proxy

    async def destroy(self):
        """Destroy the executor"""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._temp_dag_file:
            Path(self._temp_dag_file).unlink()

    async def exec_aggregation_async(
        self,
        batch_inputs: Mapping[str, Any],
        aggregation_inputs: Mapping[str, Any],
        run_id: Optional[str] = None,
    ) -> AggregationResult:
        return AggregationResult({}, {}, {})

    def _is_executor_active(self):
        """Check if the process is still running and return False if it has exited"""
        # get the exit code of the process by poll() and if it is None, it means the process is still running
        return self._process.poll() is None

    @classmethod
    def _get_tool_metadata(cls, flow_file: Path, working_dir: Path) -> dict:
        flow_tools_json_path = working_dir / PROMPT_FLOW_DIR_NAME / FLOW_TOOLS_JSON
        if flow_tools_json_path.is_file():
            with open(flow_tools_json_path, mode="r", encoding=DEFAULT_ENCODING) as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    raise MetaFileReadError(
                        message_format="Failed to fetch meta of tools: {file_path} is not a valid json file.",
                        file_path=flow_tools_json_path.absolute().as_posix(),
                    )
        raise MetaFileNotFound(
            message_format=(
                "Failed to fetch meta of tools: cannot find {file_path}, please build the flow project first."
            ),
            file_path=flow_tools_json_path.absolute().as_posix(),
        )

    @classmethod
    def find_available_port(cls) -> str:
        """Find an available port on localhost"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            _, port = s.getsockname()
            return str(port)
