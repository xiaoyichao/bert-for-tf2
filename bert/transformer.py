# coding=utf-8
#
# created by kpe on 20.Mar.2019 at 16:30
#

from __future__ import absolute_import, division, print_function

from tensorflow.python import keras

from params_flow import LayerNormalization

from bert.attention import AttentionLayer
from bert.layer import Layer


class ProjectionLayer(Layer):
    class Params(Layer.Params):
        hidden_size       = None
        hidden_dropout    = 0.1
        initializer_range = 0.02

    def _construct(self, params: Params):
        self.dense      = None
        self.dropout    = None
        self.layer_norm = None

        self.supports_masking = True

    # noinspection PyAttributeOutsideInit
    def build(self, input_shape):
        assert isinstance(input_shape, list) and 2 == len(input_shape)
        out_shape, residual_shape = input_shape
        self.input_spec = [keras.layers.InputSpec(shape=out_shape),
                           keras.layers.InputSpec(shape=residual_shape)]

        self.dense = keras.layers.Dense(units=self.params.hidden_size,
                                        kernel_initializer=self.create_initializer(),
                                        name="dense")
        self.dropout    = keras.layers.Dropout(rate=self.params.hidden_dropout)
        self.layer_norm = LayerNormalization(name="LayerNorm")

        super(ProjectionLayer, self).build(input_shape)

    def call(self, inputs, mask=None, training=None, **kwargs):
        output, residual = inputs
        output = self.dense(output)
        output = self.dropout(output, training=training)
        output = self.layer_norm(output + residual)
        return output


class TransformerSelfAttentionLayer(Layer):
    class Params(ProjectionLayer.Params,
                 AttentionLayer.Params):
        hidden_size         = None
        num_heads           = None
        hidden_dropout      = None
        attention_dropout   = 0.1
        initializer_range   = 0.02

    def _construct(self, params: Params):
        if params.hidden_size % params.num_heads != 0:
            raise ValueError("The hidden_size:[{}] is not a multiple of num_heads:[{}]".format(params.hidden_size,
                                                                                               params.num_heads))
        self.size_per_head = params.hidden_size // params.num_heads
        assert params.size_per_head is None or self.size_per_head == params.size_per_head

        self.attention_layer     = None
        self.attention_projector = None

        self.supports_masking = True

    def build(self, input_shape):
        self.input_spec = keras.layers.InputSpec(shape=input_shape)

        self.attention_layer = AttentionLayer.from_params(
            self.params,
            size_per_head=self.size_per_head,
            name="self",
        )
        self.attention_projector = ProjectionLayer.from_params(
            self.params,
            name="output",
        )

        super(TransformerSelfAttentionLayer, self).build(input_shape)

    def call(self, inputs, mask=None, training=None):
        layer_input = inputs

        #
        # TODO: is it OK to recompute the 3D attention mask in each attention layer
        #
        attention_head   = self.attention_layer(layer_input, mask=mask, training=training)
        attention_output = self.attention_projector([attention_head, layer_input], mask=mask, training=training)

        return attention_output


class SingleTransformerEncoderLayer(Layer):
    """
    Multi-headed, single layer for the Transformer from 'Attention is All You Need' (arXiv: 1706.03762).

    See also: https://github.com/tensorflow/tensor2tensor/blob/master/tensor2tensor/models/transformer.py
    """

    class Params(TransformerSelfAttentionLayer.Params,
                 ProjectionLayer.Params):
        intermediate_size       = None
        intermediate_activation = "gelu"

    def _construct(self, params: Params):
        if params.hidden_size % params.num_heads != 0:
            raise ValueError("The hidden_size:[{}] is not a multiple of num_heads:[{}]".format(params.hidden_size,
                                                                                               params.num_heads))
        self.size_per_head = params.hidden_size // params.num_heads

        self.self_attention      = None
        self.intermediate_layer  = None
        self.output_projector    = None

        self.supports_masking = True

    def build(self, input_shape):
        self.input_spec = keras.layers.InputSpec(shape=input_shape)  # [B, seq_len, hidden_size]

        self.self_attention_layer = TransformerSelfAttentionLayer.from_params(
            self.params,
            name="attention"
        )
        self.intermediate_layer = keras.layers.Dense(
            name="intermediate",
            units=self.params.intermediate_size,
            activation=self.get_activation(self.params.intermediate_activation),
            kernel_initializer=self.create_initializer()
        )
        self.output_projector = ProjectionLayer.from_params(
            self.params,
            name="output",
        )

        super(SingleTransformerEncoderLayer, self).build(input_shape)

    def call(self, inputs, mask=None, training=None):
        layer_input = inputs

        attention_output    = self.self_attention_layer(layer_input, mask=mask, training=training)

        # intermediate
        intermediate_output = self.intermediate_layer(attention_output)

        # output
        layer_output = self.output_projector([intermediate_output, attention_output], mask=mask)

        return layer_output


class TransformerEncoderLayer(Layer):
    """
    Multi-headed, multi-layer Transformer from 'Attention is All You Need' (arXiv: 1706.03762).

    See also: https://github.com/tensorflow/tensor2tensor/blob/master/tensor2tensor/models/transformer.py
    """

    class Params(SingleTransformerEncoderLayer.Params):
        num_layers    = None

    def _construct(self, params: Params):
        self.encoder_layers = []
        self.supports_masking = True

    def build(self, input_shape):
        self.input_spec = keras.layers.InputSpec(shape=input_shape)

        params = self.params

        # create all transformer encoder sub-layers
        self.encoder_layers = []
        for layer_ndx in range(params.num_heads):
            encoder_layer = SingleTransformerEncoderLayer.from_params(
                self.params,
                name="layer_{}".format(layer_ndx),
            )
            self.encoder_layers.append(encoder_layer)

        super(TransformerEncoderLayer, self).build(input_shape)

    def call(self, inputs, mask=None, training=None):
        layer_output = inputs

        layer_outputs = []
        for encoder_layer in self.encoder_layers:
            layer_input = layer_output

            layer_output = encoder_layer(layer_input, mask=mask, training=training)
            layer_outputs.append(layer_output)

        # return the final layer only
        final_output = layer_output

        return final_output

