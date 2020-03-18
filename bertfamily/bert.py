#! -*- coding: utf-8 -*-
"""
此处为Transformer基类及xbert变种模型的实现
"""

import numpy as np
from bertfamily.layers import *
from collections import OrderedDict
from keras.models import Model
import warnings
import json


class Transformer(object):
    """
    Transformer模型作为xbert模型的基类
    """

    def __init__(
            self,
            vocab_size,          # 词表大小
            hidden_size,         # 编码维度
            num_hidden_layers,   # Transformer总层数
            num_attention_heads, # Attention的头数
            intermediate_size,   # FeedForward的隐层维度
            hidden_act,          # FeedForward隐层的激活函数
            dropout_rate,        # Dropout比例
            embedding_size=None, # 是否指定embedding_size
            keep_tokens=None,    # 要保留的词ID列表
            layers=None,         # 外部传入的Keras层
            name=None,           # 模型名称
            **kwargs):
        if keep_tokens is None:
            self.vocab_size = vocab_size
        else:
            self.vocab_size = len(keep_tokens)
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.attention_head_size = hidden_size // num_attention_heads
        self.intermediate_size = intermediate_size
        self.dropout_rate = dropout_rate
        self.hidden_act = hidden_act
        self.embedding_size = embedding_size or hidden_size
        self.keep_tokens = keep_tokens
        self.attention_mask = None
        self.position_bias = None
        self.layers = {} if layers is None else layers
        self.name = name

    def build(self,
              layer_norm_cond=None,
              layer_norm_cond_hidden_size=None,
              layer_norm_cond_hidden_act=None,
              additional_input_layers=None,
              **kwargs):
        """
        模型构建函数
        layer_norm_*系列参数为实现Conditional Layer Normalization时使用，
        用来实现以“固定长度向量”为条件的条件Bert。
        """
        # Input
        inputs = self.prepare_inputs()
        self.set_inputs(inputs, additional_input_layers)
        outputs = inputs
        # Other
        self.layer_norm_conds = [
            layer_norm_cond,
            layer_norm_cond_hidden_size,
            layer_norm_cond_hidden_act or 'linear',
        ]
        # Embedding
        outputs = self.prepare_embeddings(outputs)
        # Main
        for i in range(self.num_hidden_layers):
            outputs = self.prepare_main_layers(outputs, i)
        # Final
        outputs = self.prepare_final_layers(outputs)
        self.set_outputs(outputs)
        # Model
        self.model = Model(self.inputs, self.outputs, name=self.name)

    def call(self, inputs, layer=None, arguments=None, **kwargs):
        """
        通过call调用层会自动重用同名层
        inputs: 上一层的输出；
        layer: 要调用的层类名；
        arguments: 传递给layer.call的参数；
        kwargs: 传递给层初始化的参数。
        """
        if layer is Dropout and self.dropout_rate == 0:
            return inputs

        arguments = arguments or {}
        name = kwargs.get('name')
        if name not in self.layers:
            layer = layer(**kwargs)
            name = layer.name
            self.layers[name] = layer

        return self.layers[name](inputs, **arguments)

    def prepare_inputs(self):
        raise NotImplementedError

    def prepare_embeddings(self, inputs):
        raise NotImplementedError

    def prepare_main_layers(self, inputs, index):
        raise NotImplementedError

    def prepare_final_layers(self, inputs):
        raise NotImplementedError

    def compute_attention_mask(self, inputs=None):
        """
        定义每一层的Attention Mask
        """
        return self.attention_mask

    def compute_position_bias(self, inputs=None):
        """
        定义每一层的Position Bias（一般相对位置编码用）
        """
        return self.position_bias

    def set_inputs(self, inputs, additional_input_layers=None):
        """
        设置input和inputs属性
        """
        if inputs is None:
            inputs = []
        elif not isinstance(inputs, list):
            inputs = [inputs]

        inputs = inputs[:]
        if additional_input_layers is not None:
            if not isinstance(additional_input_layers, list):
                additional_input_layers = [additional_input_layers]
            inputs.extend(additional_input_layers)

        self.inputs = inputs
        if len(inputs) > 1:
            self.input = inputs
        else:
            self.input = inputs[0]

    def set_outputs(self, outputs):
        """
        设置output和oututs属性
        """
        if not isinstance(outputs, list):
            outputs = [outputs]

        outputs = outputs[:]
        self.outputs = outputs
        if len(outputs) > 1:
            self.output = outputs
        else:
            self.output = outputs[0]

    @property
    def initializer(self):
        """
        默认使用截断正态分布初始化
        """
        return keras.initializers.TruncatedNormal(stddev=0.02)

    def simplify(self, inputs):
        """将list中的None过滤掉
        """
        inputs = [i for i in inputs if i is not None]
        if len(inputs) == 1:
            inputs = inputs[0]

        return inputs

    def load_variable(self, checkpoint, name):
        """加载单个变量的函数
        """
        return tf.train.load_variable(checkpoint, name)

    def create_variable(self, name, value):
        """在tensorflow中创建一个变量
        """
        return tf.Variable(value, name=name)

    def variable_mapping(self):
        """构建keras层与checkpoint的变量名之间的映射表
        """
        return {}

    def load_weights_from_checkpoint(self, checkpoint, mapping=None):
        """根据mapping从checkpoint加载权重
        """
        mapping = mapping or self.variable_mapping()

        weight_value_pairs = []
        for layer, variables in mapping.items():
            layer = self.layers[layer]
            weights = layer.trainable_weights
            values = [self.load_variable(checkpoint, v) for v in variables]
            weight_value_pairs.extend(zip(weights, values))

        K.batch_set_value(weight_value_pairs)

    def save_weights_as_checkpoint(self, filename, mapping=None):
        """根据mapping将权重保存为checkpoint格式
        """
        mapping = mapping or self.variable_mapping()

        with tf.Graph().as_default():
            for layer, variables in mapping.items():
                layer = self.layers[layer]
                values = K.batch_get_value(layer.trainable_weights)
                for name, value in zip(variables, values):
                    self.create_variable(name, value)
            with tf.Session() as sess:
                sess.run(tf.global_variables_initializer())
                saver = tf.train.Saver()
                saver.save(sess, filename, write_meta_graph=False)


class BERT(Transformer):
    """
    构建BERT模型，以Transformer为基类
    """

    def __init__(
            self,
            max_position,     # 序列最大长度
            with_pool=False,  # 是否包含Pool部分
            with_nsp=False,   # 是否包含NSP部分
            with_mlm=False,   # 是否包含MLM部分
            **kwargs          # 其余参数
    ):
        super(BERT, self).__init__(**kwargs)
        self.max_position = max_position
        self.with_pool = with_pool
        self.with_pool = with_pool
        self.with_nsp = with_nsp
        self.with_mlm = with_mlm

    def prepare_inputs(self):
        """
        BERT的输入是token_ids和segment_ids
        """
        x_in = Input(shape=(None,), name='Input-Token')
        s_in = Input(shape=(None,), name='Input-Segment')
        return [x_in, s_in]

    def prepare_embeddings(self, inputs):
        """
        BERT的embedding是token、position、segment三者embedding之和
        """
        x, s = inputs
        z = self.layer_norm_conds[0]

        x = self.call(inputs=x,
                      layer=Embedding,
                      input_dim=self.vocab_size,
                      output_dim=self.embedding_size,
                      embeddings_initializer=self.initializer,
                      mask_zero=True,
                      name='Embedding-Token')
        s = self.call(inputs=s,
                      layer=Embedding,
                      input_dim=2,
                      output_dim=self.embedding_size,
                      embeddings_initializer=self.initializer,
                      name='Embedding-Segment')
        x = self.call(inputs=[x, s], layer=Add, name='Embedding-Token-Segment')
        x = self.call(inputs=x,
                      layer=PositionEmbedding,
                      input_dim=self.max_position,
                      output_dim=self.embedding_size,
                      merge_mode='add',
                      embeddings_initializer=self.initializer,
                      name='Embedding-Position')
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='Embedding-Norm')
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='Embedding-Dropout')
        if self.embedding_size != self.hidden_size:
            x = self.call(inputs=x,
                          layer=Dense,
                          units=self.hidden_size,
                          kernel_initializer=self.initializer,
                          name='Embedding-Mapping')

        return x

    def prepare_main_layers(self, inputs, index):
        """
        BERT的主体是基于Self-Attention的模块
        顺序：Att(Mutil) --> Add --> LN --> FFN --> Add --> LN
        """
        x = inputs
        z = self.layer_norm_conds[0]

        attention_name = 'Transformer-%d-MultiHeadSelfAttention' % index
        feed_forward_name = 'Transformer-%d-FeedForward' % index
        attention_mask = self.compute_attention_mask()

        # Self Attention
        xi, x, arguments = x, [x, x, x], {'a_mask': None}
        if attention_mask is not None:
            arguments['a_mask'] = True
            x.append(attention_mask)

        x = self.call(inputs=x,
                      layer=MultiHeadAttention,
                      arguments=arguments,
                      heads=self.num_attention_heads,
                      head_size=self.attention_head_size,
                      kernel_initializer=self.initializer,
                      name=attention_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % attention_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % attention_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm' % attention_name)

        # Feed Forward
        xi = x
        x = self.call(inputs=x,
                      layer=FeedForward,
                      units=self.intermediate_size,
                      activation=self.hidden_act,
                      kernel_initializer=self.initializer,
                      name=feed_forward_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % feed_forward_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % feed_forward_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm' % feed_forward_name)

        return x

    def prepare_final_layers(self, inputs):
        """
        根据剩余参数决定输出
        """
        x = inputs
        z = self.layer_norm_conds[0]
        outputs = [x]

        if self.with_pool or self.with_nsp:
            # Pooler部分（提取CLS向量）
            x = outputs[0]
            x = self.call(inputs=x,
                          layer=Lambda,
                          function=lambda x: x[:, 0],
                          name='Pooler')
            pool_activation = 'tanh' if self.with_pool is True else self.with_pool
            x = self.call(inputs=x,
                          layer=Dense,
                          units=self.hidden_size,
                          activation=pool_activation,
                          kernel_initializer=self.initializer,
                          name='Pooler-Dense')
            if self.with_nsp:
                # Next Sentence Prediction部分
                x = self.call(inputs=x,
                              layer=Dense,
                              units=2,
                              activation='softmax',
                              kernel_initializer=self.initializer,
                              name='NSP-Proba')
            outputs.append(x)

        if self.with_mlm:
            # Masked Language Model部分
            x = outputs[0]
            x = self.call(inputs=x,
                          layer=Dense,
                          units=self.embedding_size,
                          activation=self.hidden_act,
                          kernel_initializer=self.initializer,
                          name='MLM-Dense')
            x = self.call(inputs=self.simplify([x, z]),
                          layer=LayerNormalization,
                          conditional=(z is not None),
                          hidden_units=self.layer_norm_conds[1],
                          hidden_activation=self.layer_norm_conds[2],
                          hidden_initializer=self.initializer,
                          name='MLM-Norm')
            mlm_activation = 'softmax' if self.with_mlm is True else self.with_mlm
            x = self.call(inputs=x,
                          layer=EmbeddingDense,
                          embedding_name='Embedding-Token',
                          activation=mlm_activation,
                          name='MLM-Proba')
            outputs.append(x)

        if len(outputs) == 1:
            outputs = outputs[0]
        elif len(outputs) == 2:
            outputs = outputs[1]
        else:
            outputs = outputs[1:]

        return outputs

    def load_variable(self, checkpoint, name):
        """
        加载单个变量的函数
        """
        variable = super(BERT, self).load_variable(checkpoint, name)
        if name in [
            'bert/embeddings/word_embeddings',
            'cls/predictions/output_bias',
        ]:
            if self.keep_tokens is None:
                return variable
            else:
                return variable[self.keep_tokens]
        elif name == 'cls/seq_relationship/output_weights':
            return variable.T
        else:
            return variable

    def create_variable(self, name, value):
        """在tensorflow中创建一个变量
        """
        if name == 'cls/seq_relationship/output_weights':
            value = value.T
        return super(BERT, self).create_variable(name, value)

    def variable_mapping(self):
        """
        映射到官方BERT权重格式
        """
        mapping = {
            'Embedding-Token': ['bert/embeddings/word_embeddings'],
            'Embedding-Segment': ['bert/embeddings/token_type_embeddings'],
            'Embedding-Position': ['bert/embeddings/position_embeddings'],
            'Embedding-Norm': [
                'bert/embeddings/LayerNorm/beta',
                'bert/embeddings/LayerNorm/gamma',
            ],
            'Embedding-Mapping': [
                'bert/encoder/embedding_hidden_mapping_in/kernel',
                'bert/encoder/embedding_hidden_mapping_in/bias',
            ],
            'Pooler-Dense': [
                'bert/pooler/dense/kernel',
                'bert/pooler/dense/bias',
            ],
            'NSP-Proba': [
                'cls/seq_relationship/output_weights',
                'cls/seq_relationship/output_bias',
            ],
            'MLM-Dense': [
                'cls/predictions/transform/dense/kernel',
                'cls/predictions/transform/dense/bias',
            ],
            'MLM-Norm': [
                'cls/predictions/transform/LayerNorm/beta',
                'cls/predictions/transform/LayerNorm/gamma',
            ],
            'MLM-Proba': ['cls/predictions/output_bias'],
        }

        for i in range(self.num_hidden_layers):
            prefix = 'bert/encoder/layer_%d/' % i
            mapping.update({
                'Transformer-%d-MultiHeadSelfAttention' % i: [
                    prefix + 'attention/self/query/kernel',
                    prefix + 'attention/self/query/bias',
                    prefix + 'attention/self/key/kernel',
                    prefix + 'attention/self/key/bias',
                    prefix + 'attention/self/value/kernel',
                    prefix + 'attention/self/value/bias',
                    prefix + 'attention/output/dense/kernel',
                    prefix + 'attention/output/dense/bias',
                ],
                'Transformer-%d-MultiHeadSelfAttention-Norm' % i: [
                    prefix + 'attention/output/LayerNorm/beta',
                    prefix + 'attention/output/LayerNorm/gamma',
                ],
                'Transformer-%d-FeedForward' % i: [
                    prefix + 'intermediate/dense/kernel',
                    prefix + 'intermediate/dense/bias',
                    prefix + 'output/dense/kernel',
                    prefix + 'output/dense/bias',
                ],
                'Transformer-%d-FeedForward-Norm' % i: [
                    prefix + 'output/LayerNorm/beta',
                    prefix + 'output/LayerNorm/gamma',
                ],
            })

        mapping = {k: v for k, v in mapping.items() if k in self.layers}

        return mapping


class ALBERT(BERT):
    """
    构建ALBERT模型,以bert为基类
    """

    def prepare_main_layers(self, inputs, index):
        """
        ALBERT的主体是基于Self-Attention的模块
        顺序：Att(Mutil) --> Add --> LN --> FFN --> Add --> LN
        """
        x = inputs
        z = self.layer_norm_conds[0]

        attention_name = 'Transformer-MultiHeadSelfAttention'
        feed_forward_name = 'Transformer-FeedForward'
        attention_mask = self.compute_attention_mask(0)

        # Self Attention
        xi, x, arguments = x, [x, x, x], {'a_mask': None}
        if attention_mask is not None:
            arguments['a_mask'] = True
            x.append(attention_mask)

        x = self.call(inputs=x,
                      layer=MultiHeadAttention,
                      arguments=arguments,
                      heads=self.num_attention_heads,
                      head_size=self.attention_head_size,
                      kernel_initializer=self.initializer,
                      name=attention_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % attention_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % attention_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm' % attention_name)

        # Feed Forward
        xi = x
        x = self.call(inputs=x,
                      layer=FeedForward,
                      units=self.intermediate_size,
                      activation=self.hidden_act,
                      kernel_initializer=self.initializer,
                      name=feed_forward_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % feed_forward_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % feed_forward_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm' % feed_forward_name)

        return x

    def variable_mapping(self):
        """
        映射到官方ALBERT权重格式
        """
        mapping = super(ALBERT, self).variable_mapping()

        prefix = 'bert/encoder/transformer/group_0/inner_group_0/'
        mapping.update({
            'Transformer-MultiHeadSelfAttention': [
                prefix + 'attention_1/self/query/kernel',
                prefix + 'attention_1/self/query/bias',
                prefix + 'attention_1/self/key/kernel',
                prefix + 'attention_1/self/key/bias',
                prefix + 'attention_1/self/value/kernel',
                prefix + 'attention_1/self/value/bias',
                prefix + 'attention_1/output/dense/kernel',
                prefix + 'attention_1/output/dense/bias',
            ],
            'Transformer-MultiHeadSelfAttention-Norm': [
                prefix + 'LayerNorm/beta',
                prefix + 'LayerNorm/gamma',
            ],
            'Transformer-FeedForward': [
                prefix + 'ffn_1/intermediate/dense/kernel',
                prefix + 'ffn_1/intermediate/dense/bias',
                prefix + 'ffn_1/intermediate/output/dense/kernel',
                prefix + 'ffn_1/intermediate/output/dense/bias',
            ],
            'Transformer-FeedForward-Norm': [
                prefix + 'LayerNorm_1/beta',
                prefix + 'LayerNorm_1/gamma',
            ],
        })

        mapping = {k: v for k, v in mapping.items() if k in self.layers}

        return mapping


class ALBERT_Unshared(BERT):
    """
    解开ALBERT共享约束，当成BERT用
    """
    def variable_mapping(self):
        """
        映射到官方ALBERT权重格式
        """
        mapping = super(ALBERT_Unshared, self).variable_mapping()

        prefix = 'bert/encoder/transformer/group_0/inner_group_0/'
        for i in range(self.num_hidden_layers):
            mapping.update({
                'Transformer-%d-MultiHeadSelfAttention' % i: [
                    prefix + 'attention_1/self/query/kernel',
                    prefix + 'attention_1/self/query/bias',
                    prefix + 'attention_1/self/key/kernel',
                    prefix + 'attention_1/self/key/bias',
                    prefix + 'attention_1/self/value/kernel',
                    prefix + 'attention_1/self/value/bias',
                    prefix + 'attention_1/output/dense/kernel',
                    prefix + 'attention_1/output/dense/bias',
                ],
                'Transformer-%d-MultiHeadSelfAttention-Norm' % i: [
                    prefix + 'LayerNorm/beta',
                    prefix + 'LayerNorm/gamma',
                ],
                'Transformer-%d-FeedForward' % i: [
                    prefix + 'ffn_1/intermediate/dense/kernel',
                    prefix + 'ffn_1/intermediate/dense/bias',
                    prefix + 'ffn_1/intermediate/output/dense/kernel',
                    prefix + 'ffn_1/intermediate/output/dense/bias',
                ],
                'Transformer-%d-FeedForward-Norm' % i: [
                    prefix + 'LayerNorm_1/beta',
                    prefix + 'LayerNorm_1/gamma',
                ],
            })

        mapping = {k: v for k, v in mapping.items() if k in self.layers}

        return mapping


class NEZHA(BERT):
    """
    华为推出的NAZHA模型
    链接：https://arxiv.org/abs/1909.00204
    """

    def prepare_embeddings(self, inputs):
        """NEZHA的embedding是token、segment两者embedding之和
        """
        x, s = inputs
        z = self.layer_norm_conds[0]

        x = self.call(inputs=x,
                      layer=Embedding,
                      input_dim=self.vocab_size,
                      output_dim=self.embedding_size,
                      embeddings_initializer=self.initializer,
                      mask_zero=True,
                      name='Embedding-Token')
        s = self.call(inputs=s,
                      layer=Embedding,
                      input_dim=2,
                      output_dim=self.embedding_size,
                      embeddings_initializer=self.initializer,
                      name='Embedding-Segment')
        x = self.call(inputs=[x, s], layer=Add, name='Embedding-Token-Segment')
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='Embedding-Norm')
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='Embedding-Dropout')
        if self.embedding_size != self.hidden_size:
            x = self.call(inputs=x,
                          layer=Dense,
                          units=self.hidden_size,
                          kernel_initializer=self.initializer,
                          name='Embedding-Mapping')

        return x

    def prepare_main_layers(self, inputs, index):
        """
        NEZHA的主体是基于Self-Attention的模块
        顺序：Att(Mutil) --> Add --> LN --> FFN --> Add --> LN
        """
        x = inputs
        z = self.layer_norm_conds[0]

        attention_name = 'Transformer-%d-MultiHeadSelfAttention' % index
        feed_forward_name = 'Transformer-%d-FeedForward' % index
        attention_mask = self.compute_attention_mask()
        position_bias = self.compute_position_bias(x)

        # Self Attention
        xi, x = x, [x, x, x, position_bias]
        arguments = {'a_mask': None, 'p_bias': 'typical_relative'}
        if attention_mask is not None:
            arguments['a_mask'] = True
            x.insert(3, attention_mask)

        x = self.call(inputs=x,
                      layer=MultiHeadAttention,
                      arguments=arguments,
                      heads=self.num_attention_heads,
                      head_size=self.attention_head_size,
                      kernel_initializer=self.initializer,
                      name=attention_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % attention_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % attention_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm' % attention_name)

        # Feed Forward
        xi = x
        x = self.call(inputs=x,
                      layer=FeedForward,
                      units=self.intermediate_size,
                      activation=self.hidden_act,
                      kernel_initializer=self.initializer,
                      name=feed_forward_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % feed_forward_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % feed_forward_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm' % feed_forward_name)

        return x

    def compute_position_bias(self, inputs=None):
        """经典相对位置编码
        """
        if self.position_bias is None:

            def sinusoidal(shape, dtype=None):
                """NEZHA直接使用Sin-Cos形式的位置向量
                """
                vocab_size, depth = shape
                embeddings = np.zeros(shape)
                for pos in range(vocab_size):
                    for i in range(depth // 2):
                        theta = pos / np.power(10000, 2. * i / depth)
                        embeddings[pos, 2 * i] = np.sin(theta)
                        embeddings[pos, 2 * i + 1] = np.cos(theta)
                return embeddings

            x = inputs
            self.position_bias = self.call(inputs=[x, x],
                                           layer=RelativePositionEmbedding,
                                           input_dim=2 * 64 + 1,
                                           output_dim=self.attention_head_size,
                                           embeddings_initializer=sinusoidal,
                                           name='Embedding-Relative-Position',
                                           trainable=False)

        return self.position_bias


class GPT2_ML(Transformer):
    """
    构建GPT2_ML模型
    链接: https://github.com/imcaspar/gpt2-ml
    """

    def __init__(
            self,
            max_position,  # 序列最大长度
            final_activation='softmax',  # 预测分布的激活函数
            **kwargs  # 其余参数
    ):
        super(GPT2_ML, self).__init__(**kwargs)
        self.max_position = max_position
        self.final_activation = final_activation

    def prepare_inputs(self):
        """
        GPT2_ML的输入是token_ids和segment_ids
        """
        x_in = Input(shape=(None,), name='Input-Token')
        return x_in

    def prepare_embeddings(self, inputs):
        """
        GPT2_ML的embedding是token、position两者embedding之和
        """
        x = inputs
        z = self.layer_norm_conds[0]

        x = self.call(inputs=x,
                      layer=Embedding,
                      input_dim=self.vocab_size,
                      output_dim=self.embedding_size,
                      embeddings_initializer=self.initializer,
                      mask_zero=True,
                      name='Embedding-Token')
        x = self.call(inputs=x,
                      layer=PositionEmbedding,
                      input_dim=self.max_position,
                      output_dim=self.embedding_size,
                      merge_mode='add',
                      embeddings_initializer=self.initializer,
                      name='Embedding-Position')
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      epsilon=1e-5,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='Embedding-Norm')
        if self.embedding_size != self.hidden_size:
            x = self.call(inputs=x,
                          layer=Dense,
                          units=self.hidden_size,
                          kernel_initializer=self.initializer,
                          name='Embedding-Mapping')

        return x

    def prepare_main_layers(self, inputs, index):
        """
        GPT2_ML的主体是基于Self-Attention的模块
        顺序：Att(Mutil)  --> LN --> FFN --> Add --> LN
        """
        x = inputs
        z = self.layer_norm_conds[0]

        attention_name = 'Transformer-%d-MultiHeadSelfAttention' % index
        feed_forward_name = 'Transformer-%d-FeedForward' % index
        attention_mask = self.compute_attention_mask()

        # Self Attention
        xi, x, arguments = x, [x, x, x, attention_mask], {'a_mask': True}

        x = self.call(inputs=x,
                      layer=MultiHeadAttention,
                      arguments=arguments,
                      heads=self.num_attention_heads,
                      head_size=self.attention_head_size,
                      kernel_initializer=self.initializer,
                      name=attention_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % attention_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % attention_name)

        # Feed Forward
        xi = x
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      epsilon=1e-5,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm-0' % feed_forward_name)
        x = self.call(inputs=x,
                      layer=FeedForward,
                      units=self.intermediate_size,
                      activation=self.hidden_act,
                      kernel_initializer=self.initializer,
                      name=feed_forward_name)
        x = self.call(inputs=x,
                      layer=Dropout,
                      rate=self.dropout_rate,
                      name='%s-Dropout' % feed_forward_name)
        x = self.call(inputs=[xi, x],
                      layer=Add,
                      name='%s-Add' % feed_forward_name)
        x = self.call(inputs=self.simplify([x, z]),
                      layer=LayerNormalization,
                      epsilon=1e-5,
                      conditional=(z is not None),
                      hidden_units=self.layer_norm_conds[1],
                      hidden_activation=self.layer_norm_conds[2],
                      hidden_initializer=self.initializer,
                      name='%s-Norm-1' % feed_forward_name)

        return x

    def prepare_final_layers(self, inputs):
        """剩余部分
        """
        x = inputs
        z = self.layer_norm_conds[0]

        # Language Model部分
        x = self.call(inputs=x,
                      layer=EmbeddingDense,
                      embedding_name='Embedding-Token',
                      activation=self.final_activation,
                      name='LM-Proba')

        return x

    def load_variable(self, checkpoint, name):
        """加载单个变量的函数
        """
        variable = super(GPT2_ML, self).load_variable(checkpoint, name)
        if name == 'newslm/embeddings/word_embed':
            if self.keep_tokens is None:
                return variable
            else:
                return variable[self.keep_tokens]
        else:
            return variable

    def compute_attention_mask(self, inputs=None):
        """
        添加下三角形式的attention mask
        """
        if self.attention_mask is None:
            def lm_mask(s):
                import tensorflow as tf
                seq_len = K.shape(s)[1]
                with K.name_scope('attention_mask'):
                    ones = K.ones((1, 1, seq_len, seq_len))
                a_mask = tf.linalg.band_part(ones, -1, 0)
                return a_mask

            self.attention_mask = self.call(inputs=self.inputs[0],
                                            layer=Lambda,
                                            function=lm_mask,
                                            name='Attention-LM-Mask')

        return self.attention_mask

    def variable_mapping(self):
        """
        映射到官方GPT2_ML权重格式
        """
        mapping = {
            'Embedding-Token': ['newslm/embeddings/word_embed'],
            'Embedding-Position': ['newslm/embeddings/pos_embed'],
            'Embedding-Norm': [
                'newslm/embeddings/LayerNorm_embed_norm/beta',
                'newslm/embeddings/LayerNorm_embed_norm/gamma',
            ],
        }

        for i in range(self.num_hidden_layers):
            prefix = 'newslm/layer%02d/' % i
            mapping.update({
                'Transformer-%d-MultiHeadSelfAttention' % i: [
                    prefix + 'query_layer/kernel',
                    prefix + 'query_layer/bias',
                    prefix + 'key_layer/kernel',
                    prefix + 'key_layer/bias',
                    prefix + 'value_layer/kernel',
                    prefix + 'value_layer/bias',
                    prefix + 'context_projection_layer/kernel',
                    prefix + 'context_projection_layer/bias',
                ],
                'Transformer-%d-FeedForward-Norm-0' % i: [
                    prefix + 'LayerNorm_mlp_ln0/beta',
                    prefix + 'LayerNorm_mlp_ln0/gamma',
                ],
                'Transformer-%d-FeedForward' % i: [
                    prefix + 'intermediate/kernel',
                    prefix + 'intermediate/bias',
                    prefix + 'output/kernel',
                    prefix + 'output/bias',
                ],
                'Transformer-%d-FeedForward-Norm-1' % i: [
                    prefix + 'LayerNorm_mlp_ln1/beta',
                    prefix + 'LayerNorm_mlp_ln1/gamma',
                ],
            })

        mapping = {k: v for k, v in mapping.items() if k in self.layers}

        return mapping


def extend_with_language_model(BaseModel):
    """
    添加下三角的Attention Mask（语言模型用）
    """
    class LanguageModel(BaseModel):
        """
        带下三角Attention Mask的派生模型
        """

        def __init__(self, *args, **kwargs):
            super(LanguageModel, self).__init__(*args, **kwargs)
            self.with_mlm = self.with_mlm or True

        def compute_attention_mask(self, inputs=None):
            """重载此函数即可
            """
            if self.attention_mask is None:
                def lm_mask(s):
                    import tensorflow as tf
                    seq_len = K.shape(s)[1]
                    with K.name_scope('attention_mask'):
                        ones = K.ones((1, 1, seq_len, seq_len))
                    a_mask = tf.linalg.band_part(ones, -1, 0)
                    return a_mask

                self.attention_mask = self.call(inputs=self.inputs[1],
                                                layer=Lambda,
                                                function=lm_mask,
                                                name='Attention-LM-Mask')

            return self.attention_mask

    return LanguageModel


def extend_with_unified_language_model(BaseModel):
    """
    添加UniLM的Attention Mask（UnifiedLanguageModel用）
    """
    class UnifiedLanguageModel(BaseModel):
        """
        带UniLM的Attention Mask的派生模型
        UniLM: https://arxiv.org/abs/1905.03197
        """

        def __init__(self, *args, **kwargs):
            super(UnifiedLanguageModel, self).__init__(*args, **kwargs)
            self.with_mlm = self.with_mlm or True

        def compute_attention_mask(self, inputs=None):
            """重载此函数即可
            """
            if self.attention_mask is None:
                def unilm_mask(s):
                    import tensorflow as tf
                    seq_len = K.shape(s)[1]
                    with K.name_scope('attention_mask'):
                        ones = K.ones((1, 1, seq_len, seq_len))
                    a_mask = tf.linalg.band_part(ones, -1, 0)
                    s_ex12 = K.expand_dims(K.expand_dims(s, 1), 2)
                    s_ex13 = K.expand_dims(K.expand_dims(s, 1), 3)
                    a_mask = (1 - s_ex13) * (1 - s_ex12) + s_ex13 * a_mask
                    return a_mask

                self.attention_mask = self.call(inputs=self.inputs[1],
                                                layer=Lambda,
                                                function=unilm_mask,
                                                name='Attention-UniLM-Mask')

            return self.attention_mask

    return UnifiedLanguageModel


def build_xbert_model(config_path=None,
                      checkpoint_path=None,
                      model='bert',
                      application='encoder',
                      return_keras_model=True,
                      **kwargs):
    """
    根据配置文件构建模型，可选加载checkpoint权重
    """
    config = kwargs
    if config_path is not None:
        config.update(json.load(open(config_path)))
    if 'max_position' not in config:
        config['max_position'] = config.get('max_position_embeddings')
    if 'dropout_rate' not in config:
        config['dropout_rate'] = config.get('hidden_dropout_prob')

    model, application = model.lower(), application.lower()

    models = {
        'bert': BERT,
        'albert': ALBERT,
        'albert_unshared': ALBERT_Unshared,
        'nezha': NEZHA,
        'gpt2_ml': GPT2_ML,
    }
    MODEL = models[model]

    if model:
        if application == 'lm':
            MODEL = extend_with_language_model(MODEL)
        elif application == 'unilm':
            MODEL = extend_with_unified_language_model(MODEL)

    transformer = MODEL(**kwargs)
    transformer.build(**kwargs)

    if checkpoint_path is not None:
        transformer.load_weights_from_checkpoint(checkpoint_path)

    if return_keras_model:
        return transformer.model
    else:
        return transformer

