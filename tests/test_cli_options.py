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
