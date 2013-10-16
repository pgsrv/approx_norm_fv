""" Uses approximations for both signed square rooting and l2 normalization."""
import argparse
from collections import defaultdict
from collections import namedtuple
import cPickle
import functools
from multiprocessing import Pool
from itertools import izip
import numpy as np
import pdb
import os
from scipy import sparse
import tempfile

# from ipdb import set_trace
from joblib import Memory
from sklearn.datasets.samples_generator import make_blobs
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler
from yael import threads

from dataset import Dataset
from fisher_vectors.evaluation import Evaluation
from fisher_vectors.evaluation.utils import average_precision
from fisher_vectors.model.utils import L2_normalize as exact_l2_normalize
from fisher_vectors.model.utils import power_normalize

from load_data import approximate_signed_sqrt
from load_data import load_kernels
from load_data import load_sample_data


# TODO Possible improvements:
# [ ] Share the `SliceData` data structure with the `detection.py` module. 
# [ ] Isolate the dataset configuration into another module and the loading functions.
# [ ] Isolate the utils.
# [ ] Pre-allocate test data (counts, L2 norms and scores).
# [x] Evaluate with the exact normalizations.
# [x] Use sparse matrices for masks.
# [x] Use also empirical standardization.
# [x] Load dummy data.
# [x] Parallelize per-class evaluation.


SliceData = namedtuple('SliceData', ['fisher_vectors', 'counts', 'nr_descriptors'])

    
hmdb_stab_dict = {
    'hmdb_split%d.stab' % ii :{
        'dataset_name': 'hmdb_split%d' % ii,
        'dataset_params': {
            'ip_type': 'dense5.track15mbh',
            'nr_clusters': 256,
            'suffix': '.per_slice.delta_15.stab.fold_%d' % ii,
        },
        'eval_name': 'hmdb',
        'eval_params': {
        },
        'metric': 'accuracy',
    } for ii in xrange(1, 4)}


cache_dir = os.path.expanduser('~/scratch2/tmp')
CFG = {
    'trecvid11_devt': {
        'dataset_name': 'trecvid12',
        'dataset_params': {
            'ip_type': 'dense5.track15mbh',
            'nr_clusters': 256,
            'suffix': '.per_slice.small.delta_60.skip_1',
        },
        'eval_name': 'trecvid12',
        'eval_params': {
            'split': 'devt',
        },
        'metric': 'average_precision',
    },
    'hollywood2':{
        'dataset_name': 'hollywood2',
        'dataset_params': {
            'ip_type': 'dense5.track15mbh',
            'nr_clusters': 256,
            'suffix': '.per_slice.delta_60',
        },
        'eval_name': 'hollywood2',
        'eval_params': {
        },
        'metric': 'average_precision',
    },
    'hollywood2.delta_30':{
        'dataset_name': 'hollywood2',
        'dataset_params': {
            'ip_type': 'dense5.track15mbh',
            'nr_clusters': 256,
            'suffix': '.per_slice.delta_30',
        },
        'eval_name': 'hollywood2',
        'eval_params': {
        },
        'metric': 'average_precision',
    },
    'dummy': {
        'dataset_name': '',
        'dataset_params': {
        },
        'eval_name': 'hollywood2',
        'eval_params': {
        },
        'metric': 'accuracy',
    },
    'hmdb_split1':{
        'dataset_name': 'hmdb_split1',
        'dataset_params': {
            'ip_type': 'dense5.track15mbh',
            'nr_clusters': 256,
            'suffix': '.per_slice.delta_30',
        },
        'eval_name': 'hmdb',
        'eval_params': {
        },
        'metric': 'accuracy',
    },
    'cc':{
        'dataset_name': 'cc',
        'dataset_params': {
            'ip_type': 'dense5.track15mbh',
            'nr_clusters': 128,
            'suffix': '',
        },
        'eval_name': 'cc',
        'eval_params': {
        },
        'metric': 'average_precision',
    },
}
CFG.update(hmdb_stab_dict)


LOAD_SAMPLE_DATA_PARAMS = {
    'analytical_fim' : True,
    'pi_derivatives' : False,
    'sqrt_nr_descs'  : False,
    'return_info'    : True,
}


def my_cacher(*args):

    def loader(file, format):
        if format in ('cp', 'cPickle'):
            result = cPickle.load(file)
        elif format in ('np', 'numpy'):
            result = np.load(file)
        else:
            assert False
        return result

    def dumper(file, result, format):
        if format in ('cp', 'cPickle'):
            cPickle.dump(result, file)
        elif format in ('np', 'numpy'):
            np.save(file, result)
        else:
            assert False

    store_format = args

    def decorator(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            outfile = kwargs.get('outfile', tempfile.mkstemp()[1])
            if os.path.exists(outfile):
                with open(outfile, 'r') as ff:
                    return [loader(ff, sf) for sf in store_format]
            else:
                result = func(*args, **kwargs)
                with open(outfile, 'w') as ff:
                    for rr, sf in izip(result, store_format):
                        dumper(ff, rr, sf)
                return result
        return wrapped

    return decorator


@my_cacher('np', 'cp', 'np', 'np', 'cp', 'cp')
def load_dummy_data(seed, store_format=None, outfile=None):
    N_SAMPLES = 100
    N_CENTERS = 5
    N_FEATURES = 20
    K, D = 2, 5

    te_data, te_labels = make_blobs(
        n_samples=N_SAMPLES, centers=N_CENTERS,
        n_features=N_FEATURES, random_state=seed)

    te_slice_data.video_mask = sparse.csr_matrix(np.eye(N_SAMPLES))
    te_visual_word_mask = build_visual_word_mask(D, K)

    np.random.seed(seed)
    te_counts = np.random.rand(N_SAMPLES, K)
    te_l2_norms = te_data ** 2 * te_visual_word_mask
    te_labels = list(te_labels)

    return (
        te_data, te_labels, te_counts, te_l2_norms, te_video_mask,
        te_visual_word_mask)


def compute_weights(clf, xx, tr_std=None):
    weights = np.dot(clf.dual_coef_, xx[clf.support_])
    bias = clf.intercept_
    if tr_std is not None:
        weights /= tr_std
    return weights, bias


def predict(yy, weights, bias):
    return (- np.dot(yy, weights.T) + bias).squeeze()


def build_aggregation_mask(names):
    """ Mask to aggregate slice data into video data. """
    ii, idxs, seen = -1, [], []
    for name in names:
        if name not in seen:
            ii += 1
            seen.append(name)
        idxs.append(ii)
        
    nn = len(seen)
    N = len(names)
    mask = np.zeros((N, nn))
    mask[range(N), idxs] = 1

    return sparse.csr_matrix(mask.T)


def build_visual_word_mask(D, K):
    """ Mask to aggregate a Fisher vector into per visual word values. """
    I = np.eye(K)
    mask = np.hstack((I.repeat(D, axis=1), I.repeat(D, axis=1))).T
    return sparse.csr_matrix(mask)


def build_slice_agg_mask(N, n_group):
    # Build mask.
    yidxs = range(N)
    xidxs = map(int, np.array(yidxs) / n_group)

    M = np.int(np.ceil(float(N) / n_group)) 
    mask = np.zeros((M, N))
    mask[xidxs, yidxs] = 1.

    return sparse.csr_matrix(mask)


def group_data(data, nr_to_group):
    """Sums together `nr_to_group` consecutive rows of `data`.

    Parameters
    ----------
    data: array_like, shape (N, D)
        Data matrix.

    nr_to_group: int
        Number of consecutive slices that are up.

    Returns
    -------
    grouped_data: array_like, shape (M, D)
        Grouped data.

    """
    N = data.shape[0]
    return build_slice_agg_mask(N, nr_to_group) * data


def visual_word_l2_norm(fisher_vectors, visual_word_mask):
    return fisher_vectors ** 2 * visual_word_mask  # NxK


def visual_word_scores(fisher_vectors, weights, bias, visual_word_mask):
    return (- fisher_vectors * weights) * visual_word_mask  # NxK


def approx_l2_normalize(data, l2_norms, counts):
    zero_idxs = counts == 0
    masked_norms = np.ma.masked_array(l2_norms, zero_idxs)
    masked_counts = np.ma.masked_array(counts, zero_idxs)
    masked_result = masked_norms / masked_counts
    approx_l2_norm = np.sum(masked_result.filled(0), axis=1)
    return data / np.sqrt(approx_l2_norm[:, np.newaxis])


def approximate_video_scores(
    slice_scores, slice_counts, slice_l2_norms, nr_descriptors, video_mask):

    video_scores = sum_by(slice_scores, video_mask) / sum_by(nr_descriptors, video_mask)
    video_counts = sum_by(slice_counts, video_mask) / sum_by(nr_descriptors, video_mask)
    video_l2_norms = sum_by(slice_l2_norms, video_mask) / sum_by(nr_descriptors, video_mask) ** 2

    zero_idxs = video_counts == 0
    masked_scores = np.ma.masked_array(video_scores, zero_idxs)
    masked_counts = np.ma.masked_array(video_counts, zero_idxs)
    masked_l2_norms = np.ma.masked_array(video_l2_norms, zero_idxs)

    sqrt_scores = np.sum((masked_scores / np.sqrt(masked_counts)).filled(0), axis=1)
    approx_l2_norm = np.sum((masked_l2_norms / masked_counts).filled(0), axis=1)

    return sqrt_scores / np.sqrt(approx_l2_norm)


def sum_by(data, mask=None):
    """Sums together rows of `data` according to the sparse matrix `mask`. If
    `mask` is None, then sums all the rows.
    
    """
    if mask is None:
        return np.sum(data, axis=0)
    return mask * data


def expand_by(data, mask=None):
    if mask is None:
        return data
    return mask.T * data


def scale_by(data, coef, mask=None):
    """Multiplies each row of `data` by a normalized version of `coef`; the
    normalization is specified by `mask`. If `mask` is None, the normalization
    term is the sum of all elements in `coef`.

    """
    coef_ = coef[:, np.newaxis]
    return data * coef_ / expand_by(sum_by(coef_, mask), mask)


def scale_and_sum_by(data, coef, data_mask=None, coef_mask=None):
    """Combines two of the previous functions: first scales the rows of `data`
    by `coef` given the mask `coef_mask`; then aggreagtes the scaled rows of
    `data` by a possibly different mask `data_mask`.

    """
    return sum_by(scale_by(data, coef, coef_mask), data_mask)


def sum_and_scale_by(data, coef, mask=None):
    coef_ = coef[:, np.newaxis]
    return sum_by(data * coef_, mask=mask) / sum_by(coef_, mask=mask)


def sum_and_scale_by_squared(data, coef, mask=None):
    coef_ = coef[:, np.newaxis]
    return sum_by(data * (coef_ ** 2), mask=mask) / sum_by(coef_, mask=mask) ** 2


@my_cacher('np', 'np', 'np', 'np', 'cp', 'cp')
def load_slices(dataset, samples, outfile=None, verbose=0):

    counts = []
    fisher_vectors = []
    labels = []
    names = []
    nr_descs = []
    nr_slices = []

    for jj, sample in enumerate(samples):

        fv, ii, cc, info = load_sample_data(dataset, sample, **LOAD_SAMPLE_DATA_PARAMS)

        if sample.movie in names:
            continue

        nd = info['nr_descs']
        nd = nd[nd != 0]
        label = ii['label']

        #slice_agg_mask = build_slice_agg_mask(fv.shape[0], nr_slices_to_aggregate)

        #agg_fisher_vectors = scale_and_sum_by(fv, nd, data_mask=slice_agg_mask, coef_mask=slice_agg_mask)
        #agg_counts = scale_and_sum_by(cc, nd, data_mask=slice_agg_mask, coef_mask=slice_agg_mask)

        fisher_vectors.append(fv)
        counts.append(cc)
        nr_descs.append(nd)

        nr_slices.append(fisher_vectors[-1].shape[0])
        names.append(sample.movie)
        labels += [label]

        if verbose:
            print '%5d %5d %s' % (jj, nr_slices[-1], sample.movie)

    fisher_vectors = np.vstack(fisher_vectors)
    counts = np.vstack(counts)
    nr_descs = np.hstack(nr_descs)
    nr_slices = np.array(nr_slices)

    return fisher_vectors, counts, nr_descs, nr_slices, names, labels


def slice_aggregator(slice_data, nr_slices, nr_agg):

    # Generate idxs.
    idxs = []
    ii = 0
    for dd in nr_slices:
        idxs += [j / nr_agg for j in range(ii, ii + dd)]
        ii = int(np.ceil((ii + dd) / float(nr_agg))) * nr_agg

    assert len(idxs) == nr_slices.sum()
    mask = build_aggregation_mask(idxs)

    agg_fisher_vectors = scale_and_sum_by(
        slice_data.fisher_vectors, slice_data.nr_descriptors,
        data_mask=mask, coef_mask=mask)
    agg_counts = scale_and_sum_by(
        slice_data.counts, slice_data.nr_descriptors,
        data_mask=mask, coef_mask=mask)
    agg_nr_descs = sum_by(slice_data.nr_descriptors, mask)

    return SliceData(agg_fisher_vectors, agg_counts, agg_nr_descs)


def compute_average_precision(true_labels, predictions, verbose=0):
    average_precisions = []
    for ii in sorted(true_labels.keys()):

        ap = average_precision(true_labels[ii], predictions[ii])
        average_precisions.append(ap)

        if verbose:
            print "%3d %6.2f" % (ii, 100 * ap)

    if verbose:
        print '----------'

    print "mAP %6.2f" % (100 * np.mean(average_precisions))


def compute_accuracy(label_binarizer, true_labels, predictions, verbose=0):
    all_predictions = np.vstack((predictions[ii] for ii in sorted(predictions.keys())))
    all_true_labels = np.vstack((true_labels[ii] for ii in sorted(true_labels.keys())))

    label_binarizer.multilabel = False
    array_true_labels = label_binarizer.inverse_transform(all_true_labels.T)

    predicted_class = np.argmax(all_predictions, axis=0)
    print "Accuracy %6.2f" % (100 * accuracy_score(array_true_labels, predicted_class))


def evaluate_worker((
    cls, eval, tr_data, tr_scaler, slice_data, video_mask, visual_word_mask,
    prediction_type, verbose)):

    clf = eval.get_classifier(cls)
    weight, bias = compute_weights(clf, tr_data, tr_std=None)

    if prediction_type == 'approx':
        slice_vw_counts = slice_data.counts * slice_data.nr_descriptors[:, np.newaxis]
        slice_vw_l2_norms = visual_word_l2_norm(slice_data.fisher_vectors, visual_word_mask)
        slice_vw_scores = visual_word_scores(slice_data.fisher_vectors, weight, bias, visual_word_mask)
        predictions = approximate_video_scores(
            slice_vw_scores, slice_vw_counts, slice_vw_l2_norms,
            slice_data.nr_descriptors[:, np.newaxis], video_mask)
    elif prediction_type == 'exact':
        # Aggregate slice data into video data.
        video_data = (
            sum_by(slice_data.fisher_vectors, video_mask) /
            sum_by(slice_data.nr_descriptors, video_mask)[:, np.newaxis])

        # Apply exact normalization on the test video data.
        video_data = power_normalize(video_data, 0.5) 
        if tr_scaler is not None:
            video_data = tr_scaler.transform(video_data)
        video_data = exact_l2_normalize(video_data) 

        # Apply linear classifier.
        predictions = np.sum(- video_data * weight, axis=1)

    predictions += bias

    if verbose > 1:
        print cls,

    return cls, predictions


def load_normalized_tr_data(
    dataset, nr_slices_to_aggregate, l2_norm_type, empirical_standardization,
    sqrt_type, tr_outfile, verbose):

    D, K = 64, dataset.VOC_SIZE

    # Load slices for train data, because I need to propaget the empirical
    # standardization into the slice L2 norms.
    samples, _ = dataset.get_data('train')
    (slice_fisher_vectors, slice_counts,
     slice_nr_descs, nr_slices, _, tr_video_labels) = load_slices(
         dataset, samples, outfile=(tr_outfile % '_slices'), verbose=verbose)

    # Simulate `nr_slices_to_aggregate` times bigger slices than the base
    # length.
    slice_data = SliceData(slice_fisher_vectors, slice_counts, slice_nr_descs)
    agg_slice_data = slice_aggregator(
        slice_data, nr_slices, nr_slices_to_aggregate)


    # Build video mask.
    video_mask = build_aggregation_mask(
        sum([[ii] * int(np.ceil(float(nn) / nr_slices_to_aggregate))
             for ii, nn in enumerate(nr_slices)],
            []))

    # Scale slices by the number of descriptors.
    tr_slice_data = scale_by(
        agg_slice_data.fisher_vectors,
        agg_slice_data.nr_descriptors,
        mask=video_mask)

    # Aggregate Fisher vectors and counts per video.
    tr_video_data = scale_and_sum_by(
        agg_slice_data.fisher_vectors, agg_slice_data.nr_descriptors,
        data_mask=video_mask, coef_mask=video_mask)
    tr_video_counts = scale_and_sum_by(
        agg_slice_data.counts, agg_slice_data.nr_descriptors,
        data_mask=video_mask, coef_mask=video_mask)

    if verbose:
        print "Normalizing train data."
        print "\tSigned square rooting:", sqrt_type
        print "\tEmpirical standardization:", empirical_standardization
        print "\tL2 norm:", l2_norm_type

    def l2_normalize(data):
        if l2_norm_type == 'exact':
            return exact_l2_normalize(data)
        elif l2_norm_type == 'approx':
            if sqrt_type == 'none':
                counts = np.ones(tr_video_counts.shape)
            else:
                counts = tr_video_counts
            # Prepare the L2 norms using the possibly modified `tr_slice_data`.
            vw_mask = build_visual_word_mask(D, K)
            tr_video_l2_norms = sum_by(
                visual_word_l2_norm(tr_slice_data, vw_mask), mask=video_mask)
            return approx_l2_normalize(data, tr_video_l2_norms, counts)
        elif l2_norm_type == 'none':
            return data
        else:
            assert False

    def square_root(data):
        if sqrt_type == 'exact':
            return power_normalize(data, 0.5)
        elif sqrt_type == 'approx':
            return approximate_signed_sqrt(
                data, tr_video_counts, pi_derivatives=False, verbose=verbose)
        elif sqrt_type == 'none':
            return data
        else:
            assert False

    # Square rooting.
    tr_video_data = square_root(tr_video_data)

    # Empirical standardization.
    if empirical_standardization:
        scaler = StandardScaler(with_mean=False)
        tr_video_data = scaler.fit_transform(tr_video_data)
        tr_slice_data = scaler.transform(tr_slice_data)
    else:
        scaler = None

    # L2 normalization ("true" or "approx").
    tr_video_data = l2_normalize(tr_video_data)

    return tr_video_data, tr_video_labels, scaler


def evaluation(
    src_cfg, sqrt_type, empirical_standardization, l2_norm_type,
    prediction_type, nr_slices_to_aggregate=1, nr_threads=4, verbose=0):

    dataset = Dataset(CFG[src_cfg]['dataset_name'], **CFG[src_cfg]['dataset_params'])
    D, K = 64, dataset.VOC_SIZE

    if verbose:
        print "Loading train data."

    generic_tr_outfile = '/scratch2/clear/oneata/tmp/joblib/' + src_cfg + '_train%s%s.dat'
    agg_suffix = '_aggregated_%d' % nr_slices_to_aggregate
    tr_outfile = generic_tr_outfile % (agg_suffix, '%s')

    tr_video_data, tr_video_labels, tr_scaler = load_normalized_tr_data(
        dataset, nr_slices_to_aggregate, l2_norm_type,
        empirical_standardization, sqrt_type, tr_outfile, verbose)

    # Computing kernel.
    tr_kernel = np.dot(tr_video_data, tr_video_data.T)

    if verbose > 1:
        print '\tTrain data:   %dx%d.' % tr_video_data.shape
        print '\tTrain kernel: %dx%d.' % tr_kernel.shape

    if verbose:
        print "Training classifier."

    eval = Evaluation(CFG[src_cfg]['eval_name'], **CFG[src_cfg]['eval_params'])
    eval.fit(tr_kernel, tr_video_labels)

    if verbose:
        print "Loading test data."

    te_outfile = ('/scratch2/clear/oneata/tmp/joblib/' + src_cfg + '_test_%d.dat')
    te_samples, _ = dataset.get_data('test')
    CHUNK_SIZE = 1000
    visual_word_mask = build_visual_word_mask(D, K)

    true_labels = defaultdict(list)
    predictions = defaultdict(list)

    for ii, low in enumerate(xrange(0, len(te_samples), CHUNK_SIZE)):

        if verbose:
            print "\tPart %3d from %5d to %5d." % (ii, low, low + CHUNK_SIZE)
            print "\tEvaluating on %d threads." % nr_threads

        fisher_vectors, counts, nr_descs, nr_slices, _, te_labels = load_slices(
            dataset, te_samples, outfile=(te_outfile % ii), verbose=verbose)
        slice_data = SliceData(fisher_vectors, counts, nr_descs)

        agg_slice_data = slice_aggregator(slice_data, nr_slices, nr_slices_to_aggregate)
        agg_slice_data = agg_slice_data._replace(
            fisher_vectors=(agg_slice_data.fisher_vectors *
                            agg_slice_data.nr_descriptors[:, np.newaxis]))

        video_mask = build_aggregation_mask(
            sum([[ii] * int(np.ceil(float(nn) / nr_slices_to_aggregate))
                 for ii, nn in enumerate(nr_slices)],
                []))

        if verbose:
            print "\tTest data: %dx%d." % agg_slice_data.fisher_vectors.shape

        # Scale the FVs in the main program, to avoid blowing up the memory.
        if prediction_type == 'approx' and tr_scaler is not None:
            agg_slice_data = agg_slice_data._replace(
                fisher_vectors=tr_scaler.transform(agg_slice_data.fisher_vectors))

        eval_args = [
            (ii, eval, tr_video_data, tr_scaler, agg_slice_data, video_mask,
             visual_word_mask, prediction_type, verbose)
            for ii in xrange(eval.nr_classes)]
        evaluator = threads.ParallelIter(nr_threads, eval_args, evaluate_worker)

        if verbose > 1: print "\t\tClasses:",
        for ii, pd in evaluator:
            tl = eval.lb.transform(te_labels)[:, ii]
            true_labels[ii].append(tl)
            predictions[ii].append(pd)
        if verbose > 1: print 

    # Prepare labels.
    for cls in true_labels.keys(): 
        true_labels[cls] = np.hstack(true_labels[cls]).squeeze()
        predictions[cls] = np.hstack(predictions[cls]).squeeze()

    # Score results.
    metric = CFG[src_cfg]['metric']
    if metric == 'average_precision':
        compute_average_precision(true_labels, predictions, verbose=verbose)
    elif metric == 'accuracy':
        compute_accuracy(eval.lb, true_labels, predictions, verbose=verbose)
    else:
        assert False, "Unknown metric %s." % metric


def main():
    parser = argparse.ArgumentParser(
        description="Evaluating the normalization approximations.")

    parser.add_argument(
        '-d', '--dataset', required=True, choices=CFG.keys(),
        help="which dataset (use `dummy` for debugging purposes).")
    parser.add_argument(
        '--exact', action='store_true', default=False,
        help="uses exact normalizations at both train and test time.")
    parser.add_argument(
        '-e_std', '--empirical_standardization', default=False,
        action='store_true', help="normalizes data to have unit variance.")
    parser.add_argument(
        '--train_l2_norm', choices={'exact', 'approx'}, required=True,
        help="how to apply L2 normalization at train time.")
    parser.add_argument(
        '-nt', '--nr_threads', type=int, default=1, help="number of threads.")
    parser.add_argument(
        '--nr_slices_to_aggregate', type=int, default=1,
        help="aggregates consecutive FVs.")
    parser.add_argument(
        '-v', '--verbose', action='count', help="verbosity level.")
    args = parser.parse_args()

    tr_sqrt = 'approx'
    pred_type = 'approx'
    tr_l2_norm = args.train_l2_norm

    if args.exact:
        tr_sqrt = 'exact'
        tr_l2_norm = 'exact'
        pred_type = 'exact'

    evaluation(
        args.dataset, tr_sqrt, args.empirical_standardization, tr_l2_norm,
        pred_type, nr_slices_to_aggregate=args.nr_slices_to_aggregate,
        nr_threads=args.nr_threads, verbose=args.verbose)


if __name__ == '__main__':
    main()

