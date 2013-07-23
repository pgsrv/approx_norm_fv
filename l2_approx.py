# A bunch of experiments to check the L2 normalization approximation.
import argparse
from ipdb import set_trace
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np
import random

from fisher_vectors.model.utils import compute_L2_normalization


random.seed(0)
np.random.seed(0)


def print_header():
    print "%22s" % 'True value',
    print "%22s" % 'Approximated value',
    print "%22s" % 'Absolute error',
    print "%22s" % 'Relative error'
    print "%s" % ('-' * 22),
    print "%s" % ('-' * 22),
    print "%s" % ('-' * 22),
    print "%s" % ('-' * 22)


def print_errors(true_value, approx_value):
    print "%22.2f" % true_value,
    print "%22.2f" % approx_value,
    print "%22.2f" % np.abs(approx_value - true_value),
    print "%20.2f %%" % (np.abs(approx_value - true_value) / true_value * 100)


def print_footer(true_values, approx_values):
    mean_abs, std_abs, mean_rel, std_rel = mean_std_err_errors(
        true_values, approx_values)
    print (45 * " "),
    print "%22s" % ("%2.2f" % mean_abs + " +/- " + "%2.2f" % std_abs),
    print "%22s" % ("%2.2f" % mean_rel + " +/- " + "%2.2f" % std_rel)


def print_info(true_values, approx_values, verbose):
    print_header()
    if verbose >= 2:
        for true_value, approx_value in zip(true_values, approx_values):
            print_errors(true_value, approx_value)
    print_footer(true_values, approx_values)


def mean_std_err_errors(true_values, approx_values):
    """ Returns mean and standard errors (absolute and relative). """
    true_values = np.array(true_values)
    approx_values = np.array(approx_values)

    absolute_err = np.abs(true_values - approx_values)
    relative_err = absolute_err / true_values * 100

    N = np.size(absolute_err)
    return (
        np.mean(absolute_err), np.std(absolute_err) / N,
        np.mean(relative_err), np.std(relative_err) / N)


def generate_data(N, D, _type):
    """ Generates artificial data to test on the L2 approximation.

    Parameters
    ----------
    N: int
        Number of data points.

    D: int
        Dimension of data points.

    _type: str, {'independent', 'correlated', 'sparse'}
        The type of data to generate.

    """
    if _type == 'independent':
        return np.random.randn(N, D)
    elif _type.startswith('sparse'):
        try:
            kk = int(_type.split('_')[1])
        except ValueError:
            kk = int(float(_type.split('_')[1]) * D)
        xx = np.random.randn(N, D)
        return np.vstack(
            [xx[ii, random.sample(range(D), kk)] for ii in xrange(N)])
    else:
        assert False, "Unknown data type."


def experiment_L2_approx(N, D, _type, nr_repeats, verbose=0):
    true_values, approx_values = [], []
    for ii in xrange(nr_repeats):
        data = generate_data(N, D, _type)

        L2_norm_slice = compute_L2_normalization(data) / N ** 2
        L2_norm_all = compute_L2_normalization(np.atleast_2d(np.mean(data, 0)))
        L2_norm_approx = np.sum(L2_norm_slice)

        true_values.append(L2_norm_all)
        approx_values.append(L2_norm_approx)

    if verbose:
        print "N = %d; D = %d." % (N, D)
        print "Data generated:", _type
        print_info(true_values, approx_values, verbose)
        print

    return mean_std_err_errors(true_values, approx_values)


def plot(relative_error, relative_std):
    # Plot results.
    plt.figure(figsize=(8.5, 10))
    ax = plt.subplot(1, 1, 1)

    for D, dd in relative_error.iteritems():
        Ns, errors = zip(*sorted(dd.iteritems(), key=lambda tt: tt[0]))
        ax.plot(Ns, errors) 

    plt.tight_layout()

    # Put labels.
    ax.set_xlabel('Number of samples', labelpad=5)
    ax.set_ylabel('Relative error', labelpad=5)

    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Experiments to test the L2 norm approximation.")

    parser.add_argument(
        '-N', '--nr_samples', nargs='+', required=True,
        help="number of samples.")
    parser.add_argument(
        '-D', '--nr_dimensions', nargs='+', required=True,
        help="number of dimensions.")
    parser.add_argument(
        '--nr_repeats', type=int, default=10,
        help="number of times to repeat an experiment.")
    parser.add_argument(
        '--sampling_type', default='independent',
        help="how the data is generated (inpendent, correlated, sparse).")
    parser.add_argument(
        '--plot', default=False, action='store_true', help="generate plots.")
    parser.add_argument(
        '-v', '--verbose', action='count', help="verbosity level.")

    args = parser.parse_args()

    Ns = np.array(map(int, args.nr_samples))
    Ds = np.array(map(int, args.nr_dimensions))

    # Get results.
    relative_error = {}
    relative_std = {}

    for D in Ds:

        relative_error[D] = {}
        relative_std[D] = {}

        for N in Ns:
            _, _, mean_rel, std_rel = experiment_L2_approx(
                N, D, args.sampling_type, args.nr_repeats, args.verbose)

            relative_error[D][N] = mean_rel
            relative_std[D][N] = std_rel

    if args.plot:
        plot(relative_error, relative_std)


if __name__ == '__main__':
    main()