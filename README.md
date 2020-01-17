# bertfamily
用Keras实现更轻量级的bert及其变种


## 使用
安装方式：
```shell
pip install git+https://github.com/xuyingjie521/bertfamily.git
```
理论上兼容Python2和Python3，实验环境是Python 2.7、Tesorflow 1.13+以及Keras 2.2.4（也可在2.2.4、2.3.0、2.3.1、tf.keras下使用，建议试用稳定的2.2.4版本）

## 可加载权重
目前可支持加载的权重：
- <strong>Google原版bert</strong>: https://github.com/google-research/bert
- <strong>哈工大版roberta</strong>: https://github.com/ymcui/Chinese-BERT-wwm
- <strong>Google原版albert</strong>: https://github.com/google-research/ALBERT

## 鸣谢
- 感谢CyberZHG大佬实现的keras-bert，本实现有不少地方参考了keras-bert的源码，在此衷心感谢大佬的无私奉献。
- 感谢苏剑林|BoJone大佬实现的keras4bert，本实现有不少地方参考了keras4bert的源码，在此衷心感谢大佬的无私奉献。