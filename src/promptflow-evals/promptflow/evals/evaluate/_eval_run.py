# ---------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# ---------------------------------------------------------
from typing import Any, Dict, Optional, Type

import dataclasses
import json
import logging
import os
import posixpath
import requests
import uuid

from azure.ai.ml import MLClient
from azure.storage.blob import BlobClient
from requests.adapters import HTTPAdapter
from urllib.parse import urlparse
from urllib3.util.retry import Retry

from promptflow.evals._version import VERSION
import time

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class RunInfo():
    """
    A holder for run info, needed for logging.
    """
    run_id: str
    experiment_id: str

    @staticmethod
    def generate() -> 'RunInfo':
        """
        Generate the new RunInfo instance with the RunID and Experiment ID.
        """
        return RunInfo(
            str(uuid.uuid4()),
            str(uuid.uuid4()),
        )


class Singleton(type):
    """Singleton class, which will be used as a metaclass."""
    _instances = {}

    def __call__(cls, *args, **kwargs):
        """Redefinition of call to return one instance per type."""
        if cls not in Singleton._instances:
            Singleton._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return Singleton._instances[cls]

    @staticmethod
    def destroy(cls: Type) -> None:
        """
        Destroy the singleton instance.

        :param cls: The class to be destroyed.
        """
        Singleton._instances.pop(cls, None)


class EvalRun(metaclass=Singleton):
    '''
    The simple singleton run class, used for accessing artifact store.

    :param run_name: The name of the run.
    :param
    '''

    _MAX_RETRIES = 5
    _BACKOFF_FACTOR = 2
    _TIMEOUT = 5
    _SCOPE = "https://management.azure.com/.default"

    def __init__(self,
                 run_name: Optional[str],
                 tracking_uri: str,
                 subscription_id: str,
                 group_name: str,
                 workspace_name: str,
                 ml_client: MLClient
                 ):
        """
        Constructor
        """

        self._tracking_uri: str = tracking_uri
        self._subscription_id: str = subscription_id
        self._resource_group_name: str = group_name
        self._workspace_name: str = workspace_name
        self._ml_client: MLClient = ml_client
        self._url_base = urlparse(self._tracking_uri).netloc
        self._is_broken = self._start_run()
        self._is_terminated = False
        self.name: str = run_name if run_name else self.info.run_id

    def _get_scope(self):
        """
        Return the scope information for the workspace.

        :param workspace_object: The workspace object.
        :type workspace_object: azureml.core.workspace.Workspace
        :return: The scope information for the workspace.
        :rtype: str
        """
        return (
            "/subscriptions/{}/resourceGroups/{}/providers"
            "/Microsoft.MachineLearningServices"
            "/workspaces/{}"
        ).format(
            self._subscription_id,
            self._resource_group_name,
            self._workspace_name,
        )

    def _start_run(self) -> bool:
        """
        Make a request to start the mlflow run. If the run will not start, it will be

        marked as broken and the logging will be switched off.
        :returns: True if the run has started and False otherwise.
        """
        url = (
            f"https://{self._url_base}/mlflow/v2.0"
            f"{self._get_scope()}/api/2.0/mlflow/runs/create")
        body = {
            "experiment_id": "0",
            "user_id": "promptflow-evals",
            "start_time": int(time.time() * 1000),
            "tags": [
                {
                    "key": "mlflow.user",
                    "value": "promptflow-evals"
                }
            ]
        }
        response = self.request_with_retry(
            url=url,
            method='POST',
            json_dict=body
        )
        if response.status_code != 200:
            self.info = RunInfo.generate()
            LOGGER.error(f"The run failed to start: {response.status_code}: {response.text}."
                         "The results will be saved locally, but will not be logged to Azure.")
            return True
        parsed_response = response.json()
        self.info = RunInfo(
            run_id=parsed_response['run']['info']['run_id'],
            experiment_id=parsed_response['run']['info']['experiment_id'],
        )
        return False

    def end_run(self, status: str) -> None:
        """
        Tetminate the run.

        :param status: One of "FINISHED" "FAILED" and "KILLED"
        :type status: str
        :raises: ValueError if the run is not in ("FINISHED", "FAILED", "KILLED")
        """
        if status not in ("FINISHED", "FAILED", "KILLED"):
            raise ValueError(
                f"Incorrect terminal status {status}. "
                "Valid statuses are \"FINISHED\", \"FAILED\" and \"KILLED\".")
        if self._is_terminated:
            LOGGER.warning("Unable to stop run because it was already terminated.")
            return
        if self._is_broken:
            LOGGER.error("Unable to stop run because the run failed to start.")
            return
        url = (
            f"https://{self._url_base}/mlflow/v2.0"
            f"{self._get_scope()}/api/2.0/mlflow/runs/update")
        body = {
            "run_uuid": self.info.run_id,
            "status": status,
            "end_time": int(time.time() * 1000),
            "run_id": self.info.run_id
        }
        response = self.request_with_retry(
            url=url,
            method='POST',
            json_dict=body
        )
        if response.status_code != 200:
            LOGGER.error("Unable to terminate the run.")
        Singleton.destroy(EvalRun)
        self._is_terminated = True

    def get_run_history_uri(self) -> str:
        """
        Return the run history service URI.
        """
        return (
            f"https://{self._url_base}"
            "/history/v1.0"
            f"{self._get_scope()}"
            f'/experimentids/{self.info.experiment_id}/runs/{self.info.run_id}'
        )

    def get_artifacts_uri(self) -> str:
        """
        Returns the url to upload the artifacts.
        """
        return self.get_run_history_uri() + '/artifacts/batch/metadata'

    def get_metrics_url(self):
        """
        Return the url needed to track the mlflow metrics.
        """
        return (
            f"https://{self._url_base}"
            "/mlflow/v2.0"
            f"{self._get_scope()}"
            f'/api/2.0/mlflow/runs/log-metric'
        )

    def _get_token(self):
        """The simple method to get token from the MLClient."""
        # This behavior mimics how the authority is taken in azureml-mlflow.
        # Note, that here we are taking authority for public cloud, however,
        # it will not work for non-public clouds.
        return self._ml_client._credential.get_token(EvalRun._SCOPE)

    def request_with_retry(
        self,
        url: str,
        method: str,
        json_dict: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None
    ) -> requests.Response:
        """
        Send the request with retries.

        :param url: The url to send the request to.
        :type url: str
        :param auth_token: Azure authentication token
        :type auth_token: str or None
        :param method: The request method to be used.
        :type method: str
        :param json_dict: The json dictionary (not serialized) to be sent.
        :type json_dict: dict.
        :return: The requests.Response object.
        """
        if headers is None:
            headers = {}
        headers['User-Agent'] = f'promptflow/{VERSION}'
        headers['Authorization'] = f'Bearer {self._get_token().token}'
        retry = Retry(
            total=EvalRun._MAX_RETRIES,
            connect=EvalRun._MAX_RETRIES,
            read=EvalRun._MAX_RETRIES,
            redirect=EvalRun._MAX_RETRIES,
            status=EvalRun._MAX_RETRIES,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            backoff_factor=EvalRun._BACKOFF_FACTOR,
            allowed_methods=None
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        return session.request(
            method,
            url,
            headers=headers,
            json=json_dict,
            timeout=EvalRun._TIMEOUT
        )

    def _log_error(self, failed_op: str, response: requests.Response) -> None:
        """
        Log the error if request was not successful.

        :param failed_op: The user-friendly message for the failed operation.
        :type failed_op: str
        :param response: The request.
        :type response: requests.Response
        """
        LOGGER.error(
            f"Unable to {failed_op}, "
            f"the request failed with status code {response.status_code}, "
            f"{response.text=}."
        )

    def log_artifact(self, artifact_folder: str) -> None:
        """
        The local implementation of mlflow-like artifact logging.

        **Note:** In the current implementation we are not using the thread pool executor
        as it is done in azureml-mlflow, instead we are just running upload in cycle as we are not
        expecting uploading a lot of artifacts.
        :param artifact_folder: The folder with artifacts to be uploaded.
        :type artifact_folder: str
        """
        if self._is_broken:
            LOGGER.error("Unable to log artifact because the run failed to start.")
            return
        # First we will list the files and the appropriate remote paths for them.
        upload_path = os.path.basename(os.path.normpath(artifact_folder))
        remote_paths = {'paths': []}
        local_paths = []

        for (root, _, filenames) in os.walk(artifact_folder):
            if root != artifact_folder:
                rel_path = os.path.relpath(root, artifact_folder)
                if rel_path != '.':
                    upload_path = posixpath.join(upload_path, rel_path)
            for f in filenames:
                remote_file_path = posixpath.join(upload_path, f)
                remote_paths['paths'].append({'path': remote_file_path})
                local_file_path = os.path.join(root, f)
                local_paths.append(local_file_path)
        # Now we need to reserve the space for files in the artifact store.
        headers = {
            'Content-Type': "application/json",
            'Accept': "application/json",
            'Content-Length': str(len(json.dumps(remote_paths))),
            'x-ms-client-request-id': str(uuid.uuid1()),
        }
        response = self.request_with_retry(
            url=self.get_artifacts_uri(),
            method='POST',
            json_dict=remote_paths,
            headers=headers
        )
        if response.status_code != 200:
            self._log_error("allocate Blob for the artifact", response)
            return
        empty_artifacts = response.json()['artifactContentInformation']
        # The response from Azure contains the URL with SAS, that allows to upload file to the
        # artifact store.
        for local, remote in zip(local_paths, remote_paths['paths']):
            artifact_loc = empty_artifacts[remote['path']]
            blob_client = BlobClient.from_blob_url(artifact_loc['contentUri'], max_single_put_size=32 * 1024 * 1024)
            with open(local, 'rb') as fp:
                blob_client.upload_blob(fp)

    def log_metric(self, key: str, value: float) -> None:
        """
        Log the metric to azure silmilar to how it is done by mlflow.

        :param key: The metric name to be logged.
        :type key: str
        :param value: The valure to be logged.
        :type value: float
        """
        if self._is_broken:
            LOGGER.error("Unable to log metric because the run failed to start.")
            return
        body = {
            "run_uuid": self.info.run_id,
            "key": key,
            "value": value,
            "timestamp": int(time.time() * 1000),
            "step": 0,
            "run_id": self.info.run_id
        }
        response = self.request_with_retry(
            url=self.get_metrics_url(),
            method='POST',
            json_dict=body,
        )
        if response.status_code != 200:
            self._log_error('save metrics', response)

    @staticmethod
    def get_instance(*args, **kwargs) -> "EvalRun":
        """
        The convenience method to the the EvalRun instance.

        :return: The EvalRun instance.
        """
        return EvalRun(*args, **kwargs)
