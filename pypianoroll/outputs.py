"""Output interfaces.

Functions
---------

- save
- to_pretty_midi
- write

Variable
--------

- DEFAULT_TEMPO

"""
import json
import zipfile
from copy import deepcopy
from operator import attrgetter
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Union
import numpy as np
import pretty_midi
from pretty_midi import Instrument, PrettyMIDI

from .track import BinaryTrack, StandardTrack
from .utils import decompose_sparse

if TYPE_CHECKING:
    from .multitrack import Multitrack

__all__ = ["save", "to_pretty_midi", "write", "DEFAULT_TEMPO"]

DEFAULT_TEMPO = 120
import json
class EncodeFromNumpy(json.JSONEncoder):
    """
    - Serializes python/Numpy objects via customizing json encoder.
    - **Usage**
        - `json.dumps(python_dict, cls=EncodeFromNumpy)` to get json string.
        - `json.dump(*args, cls=EncodeFromNumpy)` to create a file.json.
    """
    def default(self, obj):
        import numpy
        if isinstance(obj, numpy.ndarray):
            return {
                "_kind_": "ndarray",
                "_value_": obj.tolist()
            }
        if isinstance(obj, numpy.integer):
            return int(obj)
        elif isinstance(obj, numpy.floating):
            return float(obj)
        elif isinstance(obj,range):
            value = list(obj)
            return {
                "_kind_" : "range",
                "_value_" : [value[0],value[-1]+1]
            }
        return super(EncodeFromNumpy, self).default(obj)



class DecodeToNumpy(json.JSONDecoder):
    """
    - Deserilizes JSON object to Python/Numpy's objects.
    - **Usage**
        - `json.loads(json_string,cls=DecodeToNumpy)` from string, use `json.load()` for file.
    """
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        import numpy
        if '_kind_' not in obj:
            return obj
        kind = obj['_kind_']
        if kind == 'ndarray':
            return numpy.array(obj['_value_'])
        elif kind == 'range':
            value = obj['_value_']
            return range(value[0],value[-1])
        return obj


def save(
    path: Union[str, Path], multitrack: "Multitrack", compressed: bool = True
):
    """Save a Multitrack object to a NPZ file.

    Parameters
    ----------
    path : str or Path
        Path to the NPZ file to save.
    multitrack : :class:`pypianoroll.Multitrack`
        Multitrack to save.
    compressed : bool
        Whether to save to a compressed NPZ file. Defaults to True.

    Notes
    -----
    To reduce the file size, the piano rolls are first converted to
    instances of :class:`scipy.sparse.csc_matrix`. The component
    arrays are then collected and saved to a npz file.

    See Also
    --------
    :func:`pypianoroll.load` : Load a NPZ file into a Multitrack object.
    :func:`pypianoroll.write` : Write a Multitrack object to a MIDI
      file.

    """
    info_dict: Dict = {
        "resolution": multitrack.resolution,
        "name": multitrack.name,
    }

    array_dict = {}
    if multitrack.tempo is not None:
        array_dict["tempo"] = multitrack.tempo
    if multitrack.downbeat is not None:
        array_dict["downbeat"] = multitrack.downbeat

    for idx, track in enumerate(multitrack.tracks):
        array_dict.update(
            decompose_sparse(track.pianoroll, "pianoroll_" + str(idx))
        )
        info_dict[str(idx)] = {
            "program": track.program,
            "is_drum": track.is_drum,
            "name": track.name,
        }

    if compressed:
        np.savez_compressed(path, **array_dict)
    else:
        np.savez(path, **array_dict)

    compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "a") as zip_file:
        zip_file.writestr("info.json", json.dumps(info_dict, cls=EncodeFromNumpy), compression)


def to_pretty_midi(
    multitrack: "Multitrack",
    default_tempo: Optional[float] = None,
    default_velocity: int = 64,
) -> PrettyMIDI:
    """Return a Multitrack object as a PrettyMIDI object.

    Notes
    -----
    - Tempo changes are not supported.
    - The velocities of the converted piano rolls will be clipped to
      [0, 127].
    - Adjacent nonzero values of the same pitch will be considered
      a single note with their mean as its velocity.

    Parameters
    ----------
    default_tempo : int
        Default tempo to use. Defaults to the first element of
        attribute `tempo`.
    default_velocity : int
        Default velocity to assign to binarized tracks. Defaults to
        64.

    Returns
    -------
    :class:`pretty_midi.PrettyMIDI`
        Converted PrettyMIDI object.

    """
    # TODO: Add downbeat support -> time signature change events
    # TODO: Add tempo support -> tempo change events
    if default_tempo is not None:
        tempo = default_tempo
    elif multitrack.tempo:
        tempo = multitrack.tempo[0]
    else:
        tempo = DEFAULT_TEMPO

    # Create a PrettyMIDI instance
    midi = PrettyMIDI(initial_tempo=tempo)

    # Compute length of a time step
    time_step_length = 60.0 / tempo / multitrack.resolution

    for track in multitrack.tracks:
        instrument = Instrument(
            program=track.program, is_drum=track.is_drum, name=track.name
        )
        if isinstance(track, BinaryTrack):
            processed = track.set_nonzeros(default_velocity)
        elif isinstance(track, StandardTrack):
            copied = deepcopy(track)
            processed = copied.clip()
        else:
            raise ValueError(
                f"Expect BinaryTrack or StandardTrack, but got {type(track)}."
            )
        clipped = processed.pianoroll.astype(np.uint8)
        binarized = clipped > 0
        padded = np.pad(binarized, ((1, 1), (0, 0)), "constant")
        diff = np.diff(padded.astype(np.int8), axis=0)

        positives = np.nonzero((diff > 0).T)
        pitches = positives[0]
        note_ons = positives[1]
        note_on_times = time_step_length * note_ons
        note_offs = np.nonzero((diff < 0).T)[1]
        note_off_times = time_step_length * note_offs

        for idx, pitch in enumerate(pitches):
            velocity = np.mean(clipped[note_ons[idx] : note_offs[idx], pitch])
            note = pretty_midi.Note(
                velocity=int(velocity),
                pitch=pitch,
                start=note_on_times[idx],
                end=note_off_times[idx],
            )
            instrument.notes.append(note)

        instrument.notes.sort(key=attrgetter("start"))
        midi.instruments.append(instrument)

    return midi


def write(path: str, multitrack: "Multitrack"):
    """Write a Multitrack object to a MIDI file.

    Parameters
    ----------
    path : str
        Path to write the file.
    multitrack : :class:`pypianoroll.Multitrack`
        Multitrack to save.

    See Also
    --------
    :func:`pypianoroll.read` : Read a MIDI file into a Multitrack
      object.
    :func:`pypianoroll.save` : Save a Multitrack object to a NPZ file.

    """
    return to_pretty_midi(multitrack).write(str(path))
