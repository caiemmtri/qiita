from os.path import join
from tempfile import mkdtemp
from gzip import open as gzopen
from traceback import format_exception_only
from sys import exc_info

from .processing_pipeline import StudyPreprocessor, AddFilesToRawData
from .analysis_pipeline import RunAnalysis
from qiita_core.qiita_settings import qiita_config
from qiita_ware.commands import submit_EBI_from_files
from qiita_ware.demux import to_per_sample_ascii
from qiita_ware.exceptions import ComputeError
from qiita_ware.util import open_file
from qiita_db.study import Study
from qiita_db.analysis import Analysis
from qiita_db.metadata_template import SampleTemplate, PrepTemplate
from qiita_db.data import PreprocessedData, RawData


def preprocessor(study_id, prep_template_id, param_id, param_constructor):
    """Dispatch for preprocessor work"""
    study = Study(study_id)
    prep_template = PrepTemplate(prep_template_id)
    params = param_constructor(param_id)

    sp = StudyPreprocessor()
    try:
        preprocess_out = sp(study, prep_template, params)
    except Exception as e:
        error_msg = ''.join(format_exception_only(e, exc_info()))
        prep_template.preprocessing_status = "failed: %s" % error_msg
        preprocess_out = None

    return preprocess_out


def submit_to_ebi(study_id):
    """Submit a study to EBI"""
    study = Study(study_id)
    st = SampleTemplate(study.sample_template)

    # currently we get the last one always
    raw_data_id = study.raw_data()[-1]
    pt = PrepTemplate(raw_data_id)
    preprocessed_data = PreprocessedData(study.preprocessed_data()[-1])

    state = preprocessed_data.submitted_to_insdc_status()
    if state in ('submitting', 'success'):
        raise ValueError("Cannot resubmit! Current state is: %s" % state)

    investigation_type = RawData(raw_data_id).investigation_type

    demux = [path for path, ftype in preprocessed_data.get_filepaths()
             if ftype == 'preprocessed_demux'][0]

    tmp_dir = mkdtemp(prefix=qiita_config.working_dir)
    output_dir = tmp_dir + '_submission'

    samp_fp = join(tmp_dir, 'sample_metadata.txt')
    prep_fp = join(tmp_dir, 'prep_metadata.txt')

    st.to_file(samp_fp)
    pt.to_file(prep_fp)

    with open_file(demux) as demux_fh:
        for samp, iterator in to_per_sample_ascii(demux_fh, list(st)):
            with gzopen(join(tmp_dir, "%s.fastq.gz" % samp), 'w') as fh:
                for record in iterator:
                    fh.write(record)

    preprocessed_data.update_insdc_status('submitting')
    study_acc, submission_acc = submit_EBI_from_files(study_id, open(samp_fp),
                                                      open(prep_fp), tmp_dir,
                                                      output_dir,
                                                      investigation_type,
                                                      'ADD', True)

    if study_acc is None or submission_acc is None:
        preprocessed_data.update_insdc_status('failed')

        # this will set the job status as failed
        raise ComputeError("EBI Submission failed!")
    else:
        preprocessed_data.update_insdc_status('success', study_acc,
                                              submission_acc)

    return study_acc, submission_acc


def run_analysis(user_id, analysis_id, commands, comm_opts=None,
                 rarefaction_depth=None):
    """Run a meta-analysis"""
    analysis = Analysis(analysis_id)
    ar = RunAnalysis()
    return ar(user_id, analysis, commands, comm_opts, rarefaction_depth)


def add_files_to_raw_data(raw_data_id, filepaths):
    """Add files to raw data

    Needs to be dispachable because it moves large files
    """
    aftrd = AddFilesToRawData()
    return aftrd(raw_data_id, filepaths)