
"""
https://github.com/tensorflow/tensorflow/blob/r1.4/tensorflow/python/ops/rnn_cell_impl.py
https://github.com/tensorflow/tensorflow/blob/r1.4/tensorflow/contrib/rnn/python/ops/rnn_cell.py

BasicLSTMCell (and other RNN based cell) only for input with (batch, time).
Dynamic RNN cell can be handle input with (batch, time, input size) and dynamic sequence length.
"""

from tensorflow.python.ops import rnn_cell_impl
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops import init_ops
from tensorflow.python.ops import nn_ops
from tensorflow.python.ops import nn_impl

from tensorflow.contrib.layers import xavier_initializer

_EPSILON = 10**-4


class CustomLSTMCell(rnn_cell_impl.RNNCell):
    """Customized LSTM with several additional regularization
    Edit `BasicLSTMCell` of tensorflow.
    The implementation is based on: http://arxiv.org/abs/1409.2329.
    We add forget_bias (default: 1) to the biases of the forget gate in order to
    reduce the scale of forgetting in the beginning of the training.
    It does not allow cell clipping, a projection layer, and does not
    use peep-hole connections: it is the basic baseline.
    For advanced models, please use the full @{tf.nn.rnn_cell.LSTMCell}
    that follows.

    - layer normalization
    - recurrent dropout

    """

    def __init__(self, num_units, forget_bias=1.0, activation=None, reuse=None,
                 layer_norm=False, norm_shift=0.0, norm_gain=1.0,  # layer normalization
                 dropout_keep_prob=1.0, dropout_prob_seed=None  # recurrent dropout
                 ):
        """Initialize the basic LSTM cell.
        Args:
          num_units: int, The number of units in the LSTM cell.
          forget_bias: float, The bias added to forget gates (see above).
            Must set to `0.0` manually when restoring from CudnnLSTM-trained
            checkpoints.
          activation: Activation function of the inner states.  Default: `tanh`.
          reuse: (optional) Python boolean describing whether to reuse variables
            in an existing scope.  If not `True`, and the existing scope already has
            the given variables, an error is raised.
          When restoring from CudnnLSTM-trained checkpoints, must use
          CudnnCompatibleLSTMCell instead.
        """
        super(CustomLSTMCell, self).__init__(_reuse=reuse)
        self._num_units = num_units
        self._forget_bias = forget_bias
        self._activation = activation or math_ops.tanh

        self._layer_norm = layer_norm
        self._g = norm_gain
        self._b = norm_shift

        self._keep_prob = dropout_keep_prob
        self._seed = dropout_prob_seed

    @property
    def state_size(self):
        return rnn_cell_impl.LSTMStateTuple(self._num_units, self._num_units)

    @property
    def output_size(self):
        return self._num_units

    def _layer_normalization(self, inputs, scope):
        """
        :param inputs: (batch, shape)
        :param scope:
        :return : layer normalized inputs (batch, shape)
        """
        shape = inputs.get_shape()[-1:]
        with vs.variable_scope(scope):
            # Initialize beta and gamma for use by layer_norm.
            g = vs.get_variable("gain", shape=shape, initializer=init_ops.constant_initializer(self._g))  # (shape,)
            s = vs.get_variable("shift", shape=shape, initializer=init_ops.constant_initializer(self._b))  # (shape,)
        m, v = nn_impl.moments(inputs, [1], keep_dims=True)  # (batch,)
        normalized_input = (inputs - m) / math_ops.sqrt(v + _EPSILON)  # (batch, shape)
        return normalized_input * g + s

    def _linear(self, args):
        out_size = 4 * self._num_units
        projection_size = args.get_shape()[-1]
        weights = vs.get_variable("kernel", [projection_size, out_size], initializer=xavier_initializer(seed=0))
        out = math_ops.matmul(args, weights)
        if not self._layer_norm:
            bias = vs.get_variable("bias", initializer=[0.0] * out_size)
            out = nn_ops.bias_add(out, bias)
        return out

    def call(self, inputs, state):
        """Long short-term memory cell (LSTM).
        Args:
          inputs: `2-D` tensor with shape `[batch_size x input_size]`.
          state: An `LSTMStateTuple` of state tensors, each shaped
            `[batch_size x self.state_size]`, if `state_is_tuple` has been set to
            `True`.  Otherwise, a `Tensor` shaped
            `[batch_size x 2 * self.state_size]`.
        Returns:
          A pair containing the new hidden state, and the new state (either a
            `LSTMStateTuple` or a concatenated state, depending on
            `state_is_tuple`).
        Pep8 inspection appears since this signature is not same as `call` in tensorflow/python/layers/base.
            https://github.com/tensorflow/tensorflow/blob/master/tensorflow/python/layers/base.py
        """

        c, h = state  # memory cell, hidden unit
        args = array_ops.concat([inputs, h], 1)
        concat = self._linear(args)

        i, j, f, o = array_ops.split(value=concat, num_or_size_splits=4, axis=1)
        if self._layer_norm:
            i = self._layer_normalization(i, "input")
            j = self._layer_normalization(j, "transform")
            f = self._layer_normalization(f, "forget")
            o = self._layer_normalization(o, "output")
        g = self._activation(j)  # gating

        # recurrent dropout (dropout gating cell)
        if (not isinstance(self._keep_prob, float)) or self._keep_prob < 1:
            g = nn_ops.dropout(g, self._keep_prob, seed=self._seed)

        gated_in = math_ops.sigmoid(i) * g
        memory = c * math_ops.sigmoid(f + self._forget_bias)
        new_c = memory + gated_in

        # layer normalization for memory cell (original paper didn't use for memory cell).
        # if self._layer_norm:
        #     new_c = self._layer_normalization(new_c, "state")

        new_h = self._activation(new_c) * math_ops.sigmoid(o)
        new_state = rnn_cell_impl.LSTMStateTuple(new_c, new_h)
        return new_h, new_state
