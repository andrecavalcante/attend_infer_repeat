import os.path as osp
import time

import matplotlib
import numpy as np
import tensorflow as tf
from tensorflow.python.util import nest

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def rect(bbox, c=None, facecolor='none', label=None, ax=None, line_width=1):
    r = Rectangle((bbox[1], bbox[0]), bbox[3], bbox[2], linewidth=line_width,
                  edgecolor=c, facecolor=facecolor, label=label)

    if ax is not None:
        ax.add_patch(r)
    return r


def rect_stn(ax, width, height, stn_params, c=None, line_width=3):
    sx, sy, tx, ty = stn_params
    x = width * (1. - sx + tx) / 2
    y = height * (1. - sy + ty) / 2
    bbox = [y - .5, x - .5, height * sy, width * sx]
    rect(bbox, c, ax=ax, line_width=line_width)


def make_fig(air, sess, checkpoint_dir=None, global_step=None, n_samples=10, dpi=300):
    n_steps = air.max_steps

    xx, pred_canvas, pred_crop, prob, pres, w = sess.run(
        [air.obs, air.canvas, air.glimpse, air.num_steps_posterior.prob()[..., 1:], air.presence, air.where])
    height, width = xx.shape[1:]

    bs = min(n_samples, air.batch_size)
    scale = 1.5
    figsize = scale * np.asarray((bs, n_steps + 2))
    fig, axes = plt.subplots(n_steps + 2, bs, figsize=figsize)

    # ground-truth
    for i, ax in enumerate(axes[0]):
        ax.imshow(xx[i], cmap='gray', vmin=0, vmax=1)

    # reconstructions with marked steps
    for i, ax in enumerate(axes[1]):
        ax.imshow(pred_canvas[i], cmap='gray', vmin=0, vmax=1)
        for j, c in zip(xrange(n_steps), 'rgbym'):
            if pres[i, j, 0] > .5:
                rect_stn(ax, width, height, w[i, j], c, line_width=2.)
    # glimpses
    for i, ax_row in enumerate(axes[2:]):
        for j, ax in enumerate(ax_row):
            ax.imshow(pred_crop[j, i], cmap='gray')
            ax.set_title('{:d} with p({:d}) = {:.02f}'.format(int(pres[j, i, 0]), i + 1, prob[j, i].squeeze()),
                         fontsize=4 * scale)
            ax_row[0].set_ylabel('glimpse #{}'.format(i + 1))

    for ax in axes.flatten():
        ax.xaxis.set_ticks([])
        ax.yaxis.set_ticks([])

    axes[0, 0].set_ylabel('ground-truth')
    axes[1, 0].set_ylabel('reconstruction')

    if checkpoint_dir is not None:
        fig_name = osp.join(checkpoint_dir, 'progress_fig_{}.png'.format(global_step))
        fig.savefig(fig_name, dpi=dpi)
        plt.close(fig)


def make_seq_fig(air, sess, checkpoint_dir=None, global_step=None, n_samples=5, p_threshold=.5):
    # TODO: implement checkpoint_dir, global_step and n_samples
    gt, presence, w, imgs = sess.run([air.obs, air.presence, air.where, air.canvas])
    nt = imgs.shape[0]
    fig, axes = plt.subplots(2, nt, figsize=(nt*2, 4), sharex=True, sharey=True)
    axes = axes.reshape((2, nt))
    n = np.random.randint(imgs.shape[1])
    colors = 'brgc'

    for t, ax in enumerate(axes.T):
        ax[0].imshow(gt[t, n], cmap='gray', vmin=0., vmax=1.)

        pres_time = presence[t, n, :, 0]
        ps = ', '.join(['{:.2f}'.format(pp) for pp in pres_time])
        ax[1].set_title(ps)
        ax[1].imshow(imgs[t, n], cmap='gray', vmin=0., vmax=1.)
        for a in ax:
            a.grid(False)
            a.set_xticks([])
            a.set_yticks([])

        for i, (p, c) in enumerate(zip(np.greater(pres_time, p_threshold), colors)):
            if p:
                rect_stn(ax[1], 48, 48, w[t, n, i], c, line_width=1)

    axes[0, 0].set_ylabel('gt')
    axes[1, 0].set_ylabel('reconstruction')


def make_logger(air, sess, summary_writer, train_tensor, n_train_samples, test_tensor, n_test_samples):
    exprs = {
        'nelbo': air.nelbo,
        'rec_loss': air.rec_loss,
        'num_step_acc': air.num_step_accuracy,
        'num_step': air.num_step,
        'kl_div': air.kl_div
    }

    additional_exprs = {
        'nums_xe': 'nums_xe',
        'kl_num_steps': 'kl_num_steps',
        'kl_what': 'kl_what',
        'kl_where': 'kl_where',
        'baseline_loss': 'baseline_loss',
        'reinforce_loss': 'reinforce_loss',
        'l2_loss': 'l2_loss',
        'proxy_loss': 'proxy_loss',
        # 'imp_weight': 'num_steps_learning_signal'
    }

    skipped = False
    for k, v in additional_exprs.iteritems():
        try:
                exprs[k] = getattr(air, v)
        except AttributeError:
            if not skipped:
                skipped = True
                print 'make_logger: unable to log all expressions:'
            print '\tSkipping {}'.format(k)

    train_log = make_expr_logger(sess, summary_writer, n_train_samples // air.batch_size, exprs, name='train')

    data_dict = {
        train_tensor['imgs']: test_tensor['imgs'],
        train_tensor['nums']: test_tensor['nums']
    }
    test_log = make_expr_logger(sess, summary_writer, n_test_samples // air.batch_size, exprs, name='test',
                                data_dict=data_dict)

    def log(train_itr, **kwargs):
        train_log(train_itr, **kwargs)
        test_log(train_itr, **kwargs)
        print

    return log


def make_expr_logger(sess, writer, num_batches, expr_dict, name, data_dict=None,
                     constants_dict=None, measure_time=True):
    """

    :param sess:
    :param writer:
    :param num_batches:
    :param expr:
    :param name:
    :param data_dict:
    :param constants_dict:
    :return:
    """

    tags = {k: '/'.join((k, name)) for k in expr_dict}
    data_name = 'Data {}'.format(name)
    log_string = ', '.join((''.join((k + ' = {', k, ':.4f}')) for k in expr_dict))
    log_string = ' '.join(('Step {},', data_name, log_string))

    if measure_time:
        log_string += ', eval time = {:.4}s'

        def log(itr, l, t): return log_string.format(itr, t, **l)
    else:
        def log(itr, l, t): return log_string.format(itr, **l)

    def logger(itr=0, num_batches_to_eval=None, write=True):
        l = {k: 0. for k in expr_dict}
        start = time.time()
        if num_batches_to_eval is None:
            num_batches_to_eval = num_batches

        for i in xrange(num_batches_to_eval):
            if data_dict is not None:
                vals = sess.run(data_dict.values())
                feed_dict = {k: v for k, v in zip(data_dict.keys(), vals)}
                if constants_dict:
                    feed_dict.update(constants_dict)
            else:
                feed_dict = constants_dict

            r = sess.run(expr_dict, feed_dict)
            for k, v in r.iteritems():
                l[k] += v

        for k, v in l.iteritems():
            l[k] /= num_batches_to_eval
        t = time.time() - start
        print log(itr, l, t)

        if write:
            log_values(writer, itr, [tags[k] for k in l.keys()], l.values())

        return l

    return logger


def log_ratio(var_tuple, name='ratio', eps=1e-8):
    """

    :param var_tuple:
    :param name:
    :param which_name:
    :param eps:
    :return:
    """
    a, b = var_tuple
    ratio = tf.reduce_mean(abs(a) / (abs(b) + eps))
    tf.summary.scalar(name, ratio)


def log_norm(expr_list, name):
    """

    :param expr_list:
    :param name:
    :return:
    """
    n_elems = 0
    norm = 0.
    for e in nest.flatten(expr_list):
        n_elems += tf.reduce_prod(tf.shape(e))
        norm += tf.reduce_sum(e**2)
    norm /= tf.to_float(n_elems)
    tf.summary.scalar(name, norm)
    return norm


def log_values(writer, itr, tags=None, values=None, dict=None):

    if dict is not None:
        assert tags is None and values is None
        tags = dict.keys()
        values = dict.values()
    else:

        if not nest.is_sequence(tags):
            tags, values = [tags], [values]

        elif len(tags) != len(values):
            raise ValueError('tag and value have different lenghts:'
                             ' {} vs {}'.format(len(tags), len(values)))

    for t, v in zip(tags, values):
        summary = tf.Summary.Value(tag=t, simple_value=v)
        summary = tf.Summary(value=[summary])
        writer.add_summary(summary, itr)


def gradient_summaries(gvs, norm=True, ratio=True, histogram=True):
    """Register gradient summaries.

    Logs the global norm of the gradient, ratios of gradient_norm/uariable_norm and
    histograms of gradients.

    :param gvs: list of (gradient, variable) tuples
    :param norm: boolean, logs norm of the gradient if True
    :param ratio: boolean, logs ratios if True
    :param histogram: boolean, logs gradient histograms if True
    """

    with tf.name_scope('grad_summary'):
        if norm:
            grad_norm = tf.global_norm([gv[0] for gv in gvs])
            tf.summary.scalar('grad_norm', grad_norm)

        for g, v in gvs:
            var_name = v.name.split(':')[0]
            if g is None:
                print 'Gradient for variable {} is None'.format(var_name)
                continue

            if ratio:
                log_ratio((g, v), '/'.join(('grad_ratio', var_name)))

            if histogram:
                tf.summary.histogram('/'.join(('grad_hist', var_name)), g)