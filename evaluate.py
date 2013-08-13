import argparse
from ipdb import set_trace
import numpy as np

from dataset import Dataset
from fisher_vectors.evaluation import Evaluation

from load_data import load_kernels


def print_scores(scores):
    scores = [score * 100 for score in scores]
    print "mAP |",
    print " ".join(["%.2f" % score for score in scores]),
    print "| %.3f" % np.mean(scores)


def evaluate(
    tr_norms, te_norms, analytical_fim, pi_derivatives, sqrt_nr_descs,
    verbose=0):

    dataset = Dataset(
        'hollywood2', suffix='.per_slice.delta_60', nr_clusters=256)
    (tr_kernel, tr_labels,
     te_kernel, te_labels) = load_kernels(
         dataset, tr_norms=tr_norms, te_norms=te_norms,
         analytical_fim=analytical_fim, pi_derivatives=pi_derivatives,
         sqrt_nr_descs=sqrt_nr_descs)

    eval = Evaluation('hollywood2')
    scores = eval.fit(tr_kernel, tr_labels).score(te_kernel, te_labels)

    if verbose == 1:
        print "mAP: %.3f" % np.mean(scores)
    elif verbose > 1:
        print 'Train normalizations:', ', '.join(map(str, tr_norms))
        print 'Test normalizations:', ', '.join(map(str, te_norms))
        print_scores(scores)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluating the normalization approximations.")

    valid_norms = ['std', 'sqrt', 'L2', 'sqrt_cnt']

    parser.add_argument(
        '--tr_norms', choices=valid_norms, nargs='+',
        default = [],
        help="normalizations used for training.")
    parser.add_argument(
        '--te_norms', choices=valid_norms, nargs='+',
        help=("normalizations used for testing "
             "(by default, use the same as for training)."))
    parser.add_argument(
        '-afim', '--analytical_fim', action='store_true',
        help=("normalizes by the analytical form of the "
              "Fisher information matrix (FIM)."))
    parser.add_argument(
        '-dpi', '--pi_derivatives', action='store_true',
        help=("uses the derivative wrt mixing weights (default uses only "
              "derivatives wrt means and variances)."))
    parser.add_argument(
        '-sqrtT', '--sqrt_nr_descs', action='store_true',
        help="averages patch descriptors by sqrt(T) (default averages by T).")
    parser.add_argument(
        '-v', '--verbose', action='count', help="verbosity level.")

    args = parser.parse_args()

    if args.te_norms is None:
        args.te_norms = args.tr_norms
 
    evaluate(
        args.tr_norms, args.te_norms, args.analytical_fim, args.pi_derivatives,
        args.sqrt_nr_descs, verbose=args.verbose)


if __name__ == '__main__':
    main()