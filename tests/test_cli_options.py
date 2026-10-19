"""CLI option boundary regressions."""
import pytest


def test_cli_keeps_explicit_checkpoint_and_removes_unused_resume():
    from mini_swe_rsi.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        'online', '--run', '/tmp/online', '--resume-checkpoint', '/tmp/resume.json'])
    assert args.resume_checkpoint.name == 'resume.json'
    assert not hasattr(args, 'resume')

    for option in ('--resume', '--res'):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(['online', '--run', '/tmp/online', option])
        assert exc.value.code == 2


def test_cli_run_relies_on_persisted_cursor_without_resume_argument(tmp_path, monkeypatch):
    from mini_swe_rsi import cli, online, report

    seen = {}

    class Loop:
        def run(self):
            return {'status': 'completed'}

        def close(self):
            pass

    def start(*args, **kwargs):
        seen['args'] = args
        seen['kwargs'] = kwargs
        return Loop()

    monkeypatch.setattr(online.OnlineLoop, 'from_environment', start)
    monkeypatch.setattr(report, 'build_online_report', lambda root: None)
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'online', '--run', str(tmp_path / 'run')])
    cli.main()

    assert seen['kwargs'] == {}
    assert 'resume' not in seen['kwargs']


@pytest.mark.parametrize('option', ['--billing-mode', '--billing'])
def test_removed_billing_mode_is_rejected(option):
    from mini_swe_rsi.cli import build_parser

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(['online', '--run', '/tmp/online', option, 'subscription'])
    assert exc.value.code == 2


def test_prepare_uses_evaluator_environment_without_starting_online(tmp_path, monkeypatch, capsys):
    from mini_swe_rsi import cli, core, online
    seen = []
    monkeypatch.setattr(core, 'command', lambda args, **kwargs: seen.append(args) or '{"tasks":300}\n')
    monkeypatch.setattr(online.OnlineLoop, 'from_environment',
                        lambda *args, **kwargs: pytest.fail('prepare must not start model execution'))
    monkeypatch.setattr('sys.argv', ['mini-swe-rsi', 'prepare', '--dataset', 'fixture/dataset',
                                    '--split', 'test', '--revision', 'snapshot', '--data', str(tmp_path/'data')])
    cli.main()
    assert 'mini_swe_rsi.evaluation.prepare' in seen[0]
    assert seen[0][-2:] == ['--revision', 'snapshot']
    assert '300' in capsys.readouterr().out
    assert not (tmp_path/'data').exists()


@pytest.mark.parametrize('args', [
    ['online', '--run', '/tmp/run', '--dataset', 'fixture/dataset'],
    ['online', '--run', '/tmp/run', '--revision', 'snapshot'],
    ['report', '--run', '/tmp/run', '--task-limit', '3'],
    ['prepare', '--dataset', 'fixture/dataset', '--data', '/tmp/data', '--run', '/tmp/run'],
])
def test_options_for_another_command_are_rejected(args):
    from mini_swe_rsi.cli import build_parser
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(args)
    assert exc.value.code == 2
