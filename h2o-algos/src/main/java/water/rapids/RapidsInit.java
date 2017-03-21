package water.rapids;

import water.rapids.prims.word2vec.AstWord2VecToFrame;

public class RapidsInit {

  public static void registerAlgoRapids() {
    Env.init(new AstWord2VecToFrame());
  }

}
