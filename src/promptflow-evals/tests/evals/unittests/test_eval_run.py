import json
import logging
import os
import pytest

from unittest.mock import MagicMock, patch

from promptflow.evals.evaluate._eval_run import Singleton, EvalRun
from uuid import uuid4


@pytest.fixture
def setup_data():
    """Make sure, we will destroy the EvalRun instance as it is singleton."""
    yield
    Singleton._instances.clear()


@pytest.mark.unittest
class TestEvalRun:
    """Unit tests for the eval-run object."""

    @pytest.mark.parametrize(
        'status,should_raise',
        [
            ("KILLED", False),
            ("WRONG_STATUS", True),
            ("FINISHED", False),
            ("FAILED", False)
        ]
    )
    def test_end_raises(self, setup_data, status, should_raise):
        """Test that end run raises exception if incorrect status is set."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'run': {
                "info": {
                    "run_id": str(uuid4()),
                    "experiment_id": str(uuid4()),
                }
            }
        }
        mock_session.request.return_value = mock_response
        with patch('promptflow.evals.evaluate._eval_run.requests.Session', return_value=mock_session):
            run = EvalRun(
                run_name=None,
                tracking_uri='www.microsoft.com',
                subscription_id='mock',
                group_name='mock',
                workspace_name='mock',
                ml_client=MagicMock()
            )
            if should_raise:
                with pytest.raises(ValueError) as cm:
                    run.end_run(status)
                assert status in cm.value.args[0]
            else:
                run.end_run(status)

    def test_end_logs_if_fails(self, setup_data, caplog):
        """Test that if the terminal status setting was failed, it is logged."""
        mock_session = MagicMock()
        mock_response_start = MagicMock()
        mock_response_start.status_code = 200
        mock_response_start.json.return_value = {
            'run': {
                "info": {
                    "run_id": str(uuid4()),
                    "experiment_id": str(uuid4()),
                }
            }
        }
        mock_response_end = MagicMock()
        mock_response_end.status_code = 500
        mock_session.request.side_effect = [mock_response_start, mock_response_end]
        with patch('promptflow.evals.evaluate._eval_run.requests.Session', return_value=mock_session):
            logger = logging.getLogger(EvalRun.__module__)
            # All loggers, having promptflow. prefix will have "promptflow" logger
            # as a parent. This logger does not propagate the logs and cannot be
            # captured by caplog. Here we will skip this logger to capture logs.
            logger.parent = logging.root
            run = EvalRun(
                run_name=None,
                tracking_uri='www.microsoft.com',
                subscription_id='mock',
                group_name='mock',
                workspace_name='mock',
                ml_client=MagicMock()
            )
            run.end_run("FINISHED")
            assert len(caplog.records) == 1
            assert "Unable to terminate the run." in caplog.records[0].message

    def test_start_run_fails(self, setup_data, caplog):
        """Test that there are log messges if run was not started."""
        mock_session = MagicMock()
        mock_response_start = MagicMock()
        mock_response_start.status_code = 500
        mock_response_start.text = "Mock internal service error."
        mock_session.request.return_value = mock_response_start
        with patch('promptflow.evals.evaluate._eval_run.requests.Session', return_value=mock_session):
            logger = logging.getLogger(EvalRun.__module__)
            # All loggers, having promptflow. prefix will have "promptflow" logger
            # as a parent. This logger does not propagate the logs and cannot be
            # captured by caplog. Here we will skip this logger to capture logs.
            logger.parent = logging.root
            run = EvalRun(
                run_name=None,
                tracking_uri='www.microsoft.com',
                subscription_id='mock',
                group_name='mock',
                workspace_name='mock',
                ml_client=MagicMock()
            )
            assert len(caplog.records) == 1
            assert "500" in caplog.records[0].message
            assert mock_response_start.text in caplog.records[0].message
            assert 'The results will be saved locally' in caplog.records[0].message
            caplog.clear()
            # Log artifact
            run.log_artifact('test')
            assert len(caplog.records) == 1
            assert "Unable to log artifact because the run failed to start." in caplog.records[0].message
            caplog.clear()
            # Log metric
            run.log_metric('a', 42)
            assert len(caplog.records) == 1
            assert "Unable to log metric because the run failed to start." in caplog.records[0].message
            caplog.clear()
            # End run
            run.end_run("FINISHED")
            assert len(caplog.records) == 1
            assert "Unable to stop run because the run failed to start." in caplog.records[0].message
            caplog.clear()

    @patch('promptflow.evals.evaluate._eval_run.requests.Session')
    def test_singleton(self, mock_session_cls, setup_data):
        """Test that the EvalRun is actually a singleton."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = [
            {
                'run': {
                    "info": {
                        "run_id": str(uuid4()),
                        "experiment_id": str(uuid4()),
                    }
                }
            },
            {
                'run': {
                    "info": {
                        "run_id": str(uuid4()),
                        "experiment_id": str(uuid4()),
                    }
                }
            },
        ]
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        mock_session_cls.return_value = mock_session
        id1 = id(
            EvalRun(
                run_name='run',
                tracking_uri='www.microsoft.com',
                subscription_id='mock',
                group_name='mock',
                workspace_name='mock',
                ml_client=MagicMock()
            )
        )
        id2 = id(
            EvalRun(
                run_name='run',
                tracking_uri='www.microsoft.com',
                subscription_id='mock',
                group_name='mock',
                workspace_name='mock',
                ml_client=MagicMock()
            )
        )
        assert id1 == id2

    @patch('promptflow.evals.evaluate._eval_run.requests.Session')
    def test_run_name(self, mock_session_cls, setup_data):
        """Test that the run name is the same as ID if name is not given."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'run': {
                "info": {
                    "run_id": str(uuid4()),
                    "experiment_id": str(uuid4()),
                }
            }
        }
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        mock_session_cls.return_value = mock_session
        run = EvalRun(
            run_name=None,
            tracking_uri='www.microsoft.com',
            subscription_id='mock',
            group_name='mock',
            workspace_name='mock',
            ml_client=MagicMock()
        )
        assert run.info.run_id == mock_response.json.return_value['run']['info']['run_id']
        assert run.info.experiment_id == mock_response.json.return_value[
            'run']['info']['experiment_id']
        assert run.name == run.info.run_id

    @patch('promptflow.evals.evaluate._eval_run.requests.Session')
    def test_run_with_name(self, mock_session_cls, setup_data):
        """Test that the run name is not the same as id if it is given."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'run': {
                "info": {
                    "run_id": str(uuid4()),
                    "experiment_id": str(uuid4()),
                }
            }
        }
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        mock_session_cls.return_value = mock_session
        run = EvalRun(
            run_name='test',
            tracking_uri='www.microsoft.com',
            subscription_id='mock',
            group_name='mock',
            workspace_name='mock',
            ml_client=MagicMock()
        )
        assert run.info.run_id == mock_response.json.return_value['run']['info']['run_id']
        assert run.info.experiment_id == mock_response.json.return_value[
            'run']['info']['experiment_id']
        assert run.name == 'test'
        assert run.name != run.info.run_id

    @patch('promptflow.evals.evaluate._eval_run.requests.Session')
    def test_get_urls(self, mock_session_cls, setup_data):
        """Test getting url-s from eval run."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'run': {
                "info": {
                    "run_id": str(uuid4()),
                    "experiment_id": str(uuid4()),
                }
            }
        }
        mock_session = MagicMock()
        mock_session.request.return_value = mock_response
        mock_session_cls.return_value = mock_session
        run = EvalRun(
            run_name='test',
            tracking_uri=(
                'https://region.api.azureml.ms/mlflow/v2.0/subscriptions'
                '/000000-0000-0000-0000-0000000/resourceGroups/mock-rg-region'
                '/providers/Microsoft.MachineLearningServices'
                '/workspaces/mock-ws-region'),
            subscription_id='000000-0000-0000-0000-0000000',
            group_name='mock-rg-region',
            workspace_name='mock-ws-region',
            ml_client=MagicMock()
        )
        assert run.get_run_history_uri() == (
            'https://region.api.azureml.ms/history/v1.0/subscriptions'
            '/000000-0000-0000-0000-0000000/resourceGroups/mock-rg-region'
            '/providers/Microsoft.MachineLearningServices'
            '/workspaces/mock-ws-region/experimentids/'
            f'{run.info.experiment_id}/runs/{run.info.run_id}'), 'Wrong RunHistory URL'
        assert run.get_artifacts_uri() == (
            'https://region.api.azureml.ms/history/v1.0/subscriptions'
            '/000000-0000-0000-0000-0000000/resourceGroups/mock-rg-region'
            '/providers/Microsoft.MachineLearningServices'
            '/workspaces/mock-ws-region/experimentids/'
            f'{run.info.experiment_id}/runs/{run.info.run_id}'
            '/artifacts/batch/metadata'
        ), 'Wrong Artifacts URL'
        assert run.get_metrics_url() == (
            'https://region.api.azureml.ms/mlflow/v2.0/subscriptions'
            '/000000-0000-0000-0000-0000000/resourceGroups/mock-rg-region'
            '/providers/Microsoft.MachineLearningServices'
            '/workspaces/mock-ws-region/experimentids/'
            f'{run.info.experiment_id}/runs/{run.info.run_id}'), 'Wrong Metrics URL'

    @pytest.mark.parametrize(
        'log_function,expected_str',
        [
            ('log_artifact', 'allocate Blob for the artifact'),
            ('log_metric', 'save metrics')
        ]
    )
    def test_log_artifacts_logs_error(
            self,
            setup_data, tmp_path, caplog,
            log_function, expected_str
    ):
        """Test that the error is logged."""
        mock_session = MagicMock()
        mock_create_response = MagicMock()
        mock_create_response.status_code = 200
        mock_create_response.json.return_value = {
            'run': {
                "info": {
                    "run_id": str(uuid4()),
                    "experiment_id": str(uuid4()),
                }
            }
        }
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = 'Mock not found error.'

        if log_function == 'log_artifact':
            with open(os.path.join(tmp_path, 'test.json'), 'w') as fp:
                json.dump({'f1': 0.5}, fp)
        mock_session.request.side_effect = [
            mock_create_response,
            mock_response
        ]
        with patch('promptflow.evals.evaluate._eval_run.requests.Session', return_value=mock_session):
            run = EvalRun(
                run_name='test',
                tracking_uri=(
                    'https://region.api.azureml.ms/mlflow/v2.0/subscriptions'
                    '/000000-0000-0000-0000-0000000/resourceGroups/mock-rg-region'
                    '/providers/Microsoft.MachineLearningServices'
                    '/workspaces/mock-ws-region'),
                subscription_id='000000-0000-0000-0000-0000000',
                group_name='mock-rg-region',
                workspace_name='mock-ws-region',
                ml_client=MagicMock()
            )

            logger = logging.getLogger(EvalRun.__module__)
            # All loggers, having promptflow. prefix will have "promptflow" logger
            # as a parent. This logger does not propagate the logs and cannot be
            # captured by caplog. Here we will skip this logger to capture logs.
            logger.parent = logging.root
            fn = getattr(run, log_function)
            if log_function == 'log_artifact':
                kwargs = {'artifact_folder': tmp_path}
            else:
                kwargs = {'key': 'f1', 'value': 0.5}
            fn(**kwargs)
        assert len(caplog.records) == 1
        assert mock_response.text in caplog.records[0].message
        assert '404' in caplog.records[0].message
        assert expected_str in caplog.records[0].message
