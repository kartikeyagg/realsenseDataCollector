import pytest

from capture_realsense import build_parser, extrinsics_dict, intrinsics_dict, validate_args


class FakeIntrinsics:
    width = 640
    height = 480
    fx = 600.5
    fy = 601.5
    ppx = 320.0
    ppy = 240.0
    model = "brown_conrady"
    coeffs = [0, 1, 2, 3, 4]


class FakeExtrinsics:
    rotation = range(9)
    translation = [0.1, 0.2, 0.3]


def test_default_capture_profile_is_reliable_d415_profile():
    args = build_parser().parse_args([])
    assert (args.width, args.height, args.fps) == (640, 480, 30)
    assert not args.no_bag
    assert not args.no_aligned_depth


def test_intrinsics_are_json_serializable_values():
    result = intrinsics_dict(FakeIntrinsics())
    assert result["width"] == 640
    assert result["fx"] == 600.5
    assert result["coefficients"] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_extrinsics_use_metres():
    result = extrinsics_dict(FakeExtrinsics())
    assert result["rotation"] == [float(value) for value in range(9)]
    assert result["translation_metres"] == [0.1, 0.2, 0.3]


@pytest.mark.parametrize(
    "field,value",
    [("width", 0), ("height", -1), ("fps", 0), ("save_every", 0), ("warmup", -1)],
)
def test_invalid_numeric_arguments_are_rejected(field, value):
    args = build_parser().parse_args([])
    setattr(args, field, value)
    with pytest.raises(ValueError):
        validate_args(args)


def test_unsafe_session_name_is_rejected():
    args = build_parser().parse_args(["--session-name", "../escape"])
    with pytest.raises(ValueError):
        validate_args(args)
